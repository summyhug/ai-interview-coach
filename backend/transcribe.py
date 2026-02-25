"""Transcribe audio using faster-whisper. Segments = answer turns for scoring."""

import subprocess
import tempfile
from pathlib import Path

from faster_whisper import WhisperModel

_whisper_model = None


def _get_model():
    global _whisper_model
    if _whisper_model is None:
        _whisper_model = WhisperModel(
            "small",  # "base" can miss speech; "small" is more accurate (more RAM)
            device="cpu",
            compute_type="int8",
        )
    return _whisper_model


def _webm_to_wav(webm_path: str) -> str | None:
    """Convert webm to wav using ffmpeg. Returns wav path or None if ffmpeg unavailable."""
    try:
        wav_path = webm_path.rsplit(".", 1)[0] + "_converted.wav"
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-i",
                webm_path,
                "-acodec",
                "pcm_s16le",
                "-ar",
                "16000",
                "-ac",
                "1",
                wav_path,
            ],
            capture_output=True,
            check=True,
            timeout=60,
        )
        return wav_path
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        return None


def transcribe_audio(audio_path: str | Path) -> list[dict]:
    """
    Transcribe audio and return segments as turns.
    Each segment has: start, end, text.
    Merges very short segments (< 2s) with next to avoid micro-turns.
    """
    path_str = str(audio_path)
    wav_path = None
    is_webm = path_str.lower().endswith((".webm", ".ogg", ".opus"))

    # WebM/Opus often fails with PyAV; convert to WAV first if possible
    if is_webm:
        wav_path = _webm_to_wav(path_str)
        if wav_path:
            path_str = wav_path

    model = _get_model()
    try:
        segments, _ = model.transcribe(
            path_str,
            vad_filter=True,
            vad_parameters=dict(min_silence_duration_ms=500),
        )
        raw_segments = [{"start": s.start, "end": s.end, "text": s.text.strip()} for s in segments if s.text.strip()]
    except Exception as e:
        if is_webm and not wav_path:
            raise RuntimeError(
                "Could not decode WebM audio. Install ffmpeg (brew install ffmpeg) and try again."
            ) from e
        raise

    if wav_path:
        Path(wav_path).unlink(missing_ok=True)

    # Merge very short segments (< 2s) with next
    merged: list[dict] = []
    for seg in raw_segments:
        duration = seg["end"] - seg["start"]
        if merged and duration < 2.0:
            merged[-1]["text"] += " " + seg["text"]
            merged[-1]["end"] = seg["end"]
        else:
            merged.append(seg.copy())

    return merged


def transcribe_upload(file_content: bytes, suffix: str = ".webm") -> list[dict]:
    """Write uploaded bytes to temp file, transcribe, then delete."""
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
        f.write(file_content)
        path = f.name
    try:
        return transcribe_audio(path)
    finally:
        Path(path).unlink(missing_ok=True)
