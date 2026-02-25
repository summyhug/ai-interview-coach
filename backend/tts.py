"""Text-to-speech using edge-tts (Microsoft neural voices)."""

import asyncio
import tempfile
from pathlib import Path

# Default: natural-sounding English voice
DEFAULT_VOICE = "en-US-JennyNeural"


async def _generate_speech_async(text: str, voice: str = DEFAULT_VOICE) -> bytes:
    """Generate speech audio using edge-tts. Returns MP3 bytes."""
    import edge_tts
    communicate = edge_tts.Communicate(text, voice)
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
        path = f.name
    try:
        await communicate.save(path)
        return Path(path).read_bytes()
    finally:
        Path(path).unlink(missing_ok=True)


def generate_speech(text: str, voice: str = DEFAULT_VOICE) -> bytes:
    """Synchronous wrapper for edge-tts. Returns MP3 bytes."""
    return asyncio.run(_generate_speech_async(text, voice))
