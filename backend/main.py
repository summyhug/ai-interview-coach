"""FastAPI backend for AI Interview Coach."""

import logging
import os
import shutil
from pathlib import Path

from fastapi import FastAPI, File, UploadFile, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles

from transcribe import transcribe_upload
from scorer import score_turns, get_rewrites

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="AI Interview Coach")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

DEFAULT_MODEL = os.environ.get("OLLAMA_MODEL", "llama3.2")


def _fallback_scores(segments: list, message: str = "Scoring unavailable") -> dict:
    """Build fallback score structure when Ollama fails."""
    return {
        "turns": [
            {
                "turn_index": i,
                "text": s["text"],
                "direct_answer_10s": {"met": None, "note": ""},
                "specific_example": {"met": None, "note": ""},
                "quantified_impact": {"met": None, "note": ""},
                "tradeoffs": {"met": None, "note": ""},
                "crisp_takeaway": {"met": None, "note": ""},
                "filler_count": 0,
                "long_pauses": 0,
                "trailing_sentences": False,
                "question_type": "Unknown",
                "actionable_feedback": message,
            }
            for i, s in enumerate(segments)
        ],
        "overall_summary": "Could not score. Ensure Ollama is running: ollama serve",
    }


@app.get("/favicon.ico")
@app.get("/.well-known/appspecific/com.chrome.devtools.json")
async def _no_content():
    return Response(status_code=204)


@app.post("/api/analyze")
async def analyze_interview(
    audio: UploadFile = File(...),
    include_rewrites: bool = False,
):
    """
    Accept audio file, transcribe, score via Ollama, optionally get rewrites.
    """
    try:
        content = await audio.read()
        if not content:
            raise HTTPException(400, "Empty audio file")

        suffix = Path(audio.filename or "audio.webm").suffix or ".webm"
        if suffix not in {".webm", ".mp4", ".ogg", ".wav", ".mp3", ".m4a"}:
            suffix = ".webm"

        segments = transcribe_upload(content, suffix=suffix)
        if not segments:
            return {
                "segments": [],
                "scores": {"turns": [], "overall_summary": "No speech detected."},
                "rewrites": [],
            }

        try:
            scores = score_turns(segments, model=DEFAULT_MODEL)
        except Exception as e:
            logger.warning("Ollama scoring failed: %s", e)
            scores = _fallback_scores(
                segments,
                f"Scoring unavailable: {e}. Ensure Ollama is running and model pulled (ollama pull llama3.2).",
            )

        rewrites = []
        if include_rewrites and scores.get("turns"):
            context = "\n".join(
                f"Turn {i}: {s['text']}" for i, s in enumerate(segments)
            )
            weak = [t for t in scores["turns"] if not _is_strong_turn(t)][:2]
            turns_to_rewrite = weak if weak else scores["turns"][:2]
            for t in turns_to_rewrite:
                try:
                    r = get_rewrites(
                        text=t.get("text", ""),
                        model=DEFAULT_MODEL,
                        context=context,
                        turn_index=t.get("turn_index", 0),
                        question_type=t.get("question_type", "Unknown"),
                    )
                    rewrites.append({
                        "turn_index": t.get("turn_index", 0),
                        "original": t.get("text", ""),
                        "tight_45s": r["tight_45s"],
                        "expanded_2min": r["expanded_2min"],
                    })
                except Exception as e:
                    logger.warning("Rewrite failed for turn %s: %s", t.get("turn_index"), e)

        return {
            "segments": [{"start": s["start"], "end": s["end"], "text": s["text"]} for s in segments],
            "scores": scores,
            "rewrites": rewrites,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Analyze failed")
        raise HTTPException(500, str(e))


def _is_strong_turn(t: dict) -> bool:
    """Heuristic: turn is strong if most rubric items met."""
    met = 0
    for key in ("direct_answer_10s", "specific_example", "quantified_impact", "crisp_takeaway"):
        val = t.get(key)
        if isinstance(val, dict) and val.get("met") is True:
            met += 1
    return met >= 3


@app.get("/api/health")
async def health():
    return {"status": "ok"}


@app.get("/api/debug-ollama")
async def debug_ollama():
    """Test Ollama connection and raw response."""
    from ollama import chat
    from scorer import _ollama_content, _resolve_model

    try:
        model = _resolve_model(DEFAULT_MODEL)
        r = chat(
            model=model,
            messages=[{"role": "user", "content": 'Return only this JSON: {"test": true}'}],
            format="json",
        )
        content = _ollama_content(r)
        return {
            "ok": True,
            "model": model,
            "response_preview": (content or "")[:500],
        }
    except Exception as e:
        return {"ok": False, "error": str(e), "type": type(e).__name__}


@app.get("/api/check")
async def check_setup():
    """Verify Ollama and ffmpeg are available."""
    result = {"ollama": False, "ffmpeg": False, "model": DEFAULT_MODEL}
    if shutil.which("ffmpeg"):
        result["ffmpeg"] = True
    try:
        from ollama import list as ollama_list
        resp = ollama_list()
        models = [m.model for m in (resp.models or [])]
        result["ollama"] = any(DEFAULT_MODEL in m for m in models)
    except Exception:
        pass
    return result


# Serve frontend (must be after API routes so /api/* takes precedence)
frontend_path = Path(__file__).parent.parent / "frontend"
if frontend_path.exists():
    app.mount("/", StaticFiles(directory=str(frontend_path), html=True), name="frontend")
