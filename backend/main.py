"""FastAPI backend for AI Interview Coach."""

import logging
import os
import shutil
from pathlib import Path

# Load .env from project root (gitignored)
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / ".env")
except ImportError:
    pass

from fastapi import FastAPI, File, Form, Query, UploadFile, HTTPException
from fastapi.responses import Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from questions import DEFAULT_QUESTIONS, adapt_questions
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles

from transcribe import transcribe_upload
from scorer import score_turns, get_rewrites, compute_pace

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
    pace_data = compute_pace(segments)
    turns = []
    for i, s in enumerate(segments):
        t = {
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
        if i < len(pace_data):
            t["pace_wpm"] = pace_data[i]["pace_wpm"]
            t["pace_rating"] = pace_data[i]["pace_rating"]
            t["pace_feedback"] = pace_data[i]["pace_feedback"]
        turns.append(t)
    return {
        "turns": turns,
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
    question_text: str | None = Form(None),
    job_description: str | None = Form(None),
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
            scores = score_turns(
                segments,
                model=DEFAULT_MODEL,
                question_text=question_text,
                job_description=job_description,
            )
        except Exception as e:
            logger.warning("Ollama scoring failed: %s", e)
            scores = _fallback_scores(
                segments,
                f"Scoring unavailable: {e}. Ensure Ollama is running and model pulled (ollama pull llama3.2).",
            )

        # Add pace scoring to each turn
        pace_data = compute_pace(segments)
        for i, turn in enumerate(scores.get("turns", [])):
            if i < len(pace_data):
                turn["pace_wpm"] = pace_data[i]["pace_wpm"]
                turn["pace_rating"] = pace_data[i]["pace_rating"]
                turn["pace_feedback"] = pace_data[i]["pace_feedback"]

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


@app.get("/api/questions")
async def get_questions():
    """Return default 10 common questions."""
    return {"questions": DEFAULT_QUESTIONS.copy()}


class AdaptQuestionsRequest(BaseModel):
    job_description: str = ""


@app.post("/api/adapt-questions")
async def post_adapt_questions(body: AdaptQuestionsRequest):
    """Adapt questions based on job description. Returns merged list of common + tailored questions."""
    questions = adapt_questions(body.job_description, model=DEFAULT_MODEL)
    return {"questions": questions}


EDGE_VOICES = [
    ("en-US-JennyNeural", "Jenny (US, natural)"),
    ("en-US-GuyNeural", "Guy (US, male)"),
    ("en-US-SarahNeural", "Sarah (US)"),
    ("en-GB-SoniaNeural", "Sonia (UK)"),
]


@app.get("/api/tts/voices")
async def tts_voices():
    """List available Edge TTS voices."""
    return {"voices": [{"id": v[0], "label": v[1]} for v in EDGE_VOICES]}


@app.get("/api/tts")
async def text_to_speech(text: str = Query(..., min_length=1), voice: str = Query("en-US-JennyNeural")):
    """Generate speech from text using edge-tts (Microsoft neural voice). Returns MP3 audio."""
    try:
        from tts import generate_speech
        audio_bytes = generate_speech(text, voice=voice)
        return Response(content=audio_bytes, media_type="audio/mpeg")
    except ImportError:
        raise HTTPException(500, "edge-tts not installed: pip install edge-tts")
    except Exception as e:
        logger.warning("TTS failed: %s", e)
        raise HTTPException(500, str(e))


@app.get("/api/health")
async def health():
    return {"status": "ok"}


@app.get("/api/debug-llm")
async def debug_llm():
    """Test LLM connection (Gemini or Ollama)."""
    from llm import chat as llm_chat

    try:
        content, provider = llm_chat(
            messages=[{"role": "user", "content": 'Return only this JSON: {"test": true}'}],
            format_json=True,
        )
        return {
            "ok": bool(content),
            "provider": provider,
            "response_preview": (content or "")[:500],
        }
    except Exception as e:
        return {"ok": False, "error": str(e), "type": type(e).__name__}


@app.get("/api/check")
async def check_setup():
    """Verify ffmpeg and LLM (Gemini or Ollama) are available."""
    from llm import GEMINI_API_KEY

    result = {
        "ffmpeg": bool(shutil.which("ffmpeg")),
        "gemini_configured": bool(GEMINI_API_KEY),
        "ollama": False,
        "model": os.environ.get("OLLAMA_MODEL", "llama3.2"),
    }
    try:
        from ollama import list as ollama_list
        resp = ollama_list()
        models = [m.model for m in (resp.models or [])]
        result["ollama"] = any(result["model"] in m for m in models)
    except Exception:
        pass
    result["llm_ready"] = result["gemini_configured"] or result["ollama"]
    return result


# Serve frontend (must be after API routes so /api/* takes precedence)
frontend_path = Path(__file__).parent.parent / "frontend"
if frontend_path.exists():
    app.mount("/", StaticFiles(directory=str(frontend_path), html=True), name="frontend")
