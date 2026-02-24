"""Score interview turns and generate rewrites via Ollama."""

import json
import logging

from ollama import chat

logger = logging.getLogger(__name__)

# Try models in order; first available wins (prefer larger for better JSON)
DEFAULT_MODELS = ["qwen2.5", "llama3.1", "llama3.2", "mistral", "llama2"]


def _ollama_content(resp) -> str:
    """Extract content from Ollama chat response."""
    msg = getattr(resp, "message", None) or (resp.get("message") if hasattr(resp, "get") else None)
    if not msg:
        return ""
    return getattr(msg, "content", "") if not isinstance(msg, dict) else msg.get("content", "")


def _resolve_model(preferred: str) -> str:
    """Use preferred if available, else first available."""
    try:
        from ollama import list as ollama_list
        resp = ollama_list()
        models = [m.model for m in (resp.models or [])]
    except Exception:
        return preferred
    for m in models:
        if preferred in m:
            return m  # use full name e.g. llama3.2:latest
    for fallback in DEFAULT_MODELS:
        for m in models:
            if fallback in m:
                return m
    return preferred


SCORE_SYSTEM = """You are an expert interview coach. Score each answer turn using this rubric. Return ONLY valid JSON, no markdown or extra text.

Rubric per turn:
- direct_answer_10s: Did they answer the question directly in the first ~10 seconds? (true/false, note: string)
- specific_example: Did they give a specific example? (true/false, note: string)
- quantified_impact: Did they quantify impact (numbers, metrics)? (true/false, note: string)
- tradeoffs: Did they mention tradeoffs? (true/false, note: string)
- crisp_takeaway: Did they close with a crisp takeaway? (true/false, note: string)
- filler_count: Count of "um", "like", filler words
- long_pauses: Estimated long pauses (0-5 scale)
- trailing_sentences: Did they trail off or ramble? (true/false)
- question_type: One of: Behavioral, Product_sense, Technical, Estimation, Motivation, Why_this_job, Tell_me_about, Unknown
- actionable_feedback: 1-2 sentences on how to improve this answer as a job seeker (interview quality, not speech)"""

SCORE_USER_TEMPLATE = """Score these interview answer turns. Each turn is a candidate's spoken response (interviewer questions not included).

Turns:
{turns}

Return JSON in this exact shape:
{{
  "turns": [
    {{
      "turn_index": 0,
      "text": "...",
      "direct_answer_10s": {{ "met": true/false, "note": "..." }},
      "specific_example": {{ "met": true/false, "note": "..." }},
      "quantified_impact": {{ "met": true/false, "note": "..." }},
      "tradeoffs": {{ "met": true/false, "note": "..." }},
      "crisp_takeaway": {{ "met": true/false, "note": "..." }},
      "filler_count": 0,
      "long_pauses": 0,
      "trailing_sentences": true/false,
      "question_type": "...",
      "actionable_feedback": "..."
    }}
  ],
  "overall_summary": "2-3 sentences on overall performance"
}}"""

REWRITE_SYSTEM = """You are an expert interview coach. Your job is to help job seekers give BETTER interview answers—not just reword. Suggest professional, wholesome alternatives that show enthusiasm, fit, and value. Avoid generic rephrasing. Return ONLY valid JSON, no markdown or extra text."""

REWRITE_USER_TEMPLATE = """Full interview transcript (candidate answers only):
{context}

The answer to improve (turn {turn_index}):
{text}

Inferred question type: {question_type}

Provide a BETTER professional answer the job seeker could give. Not a reword—a genuinely stronger interview response. If the answer sounds desperate, negative, or unprofessional, suggest a wholesome alternative that shows enthusiasm and fit.

1. tight_45s: A ~45-second punchy version (direct, professional, confident)
2. expanded_2min: A ~2-minute version with more detail and structure

Return JSON:
{{
  "tight_45s": "...",
  "expanded_2min": "..."
}}"""


def _extract_json(text: str) -> dict | None:
    """Extract JSON from LLM response (may be wrapped in markdown or prose)."""
    if not text or not text.strip():
        return None
    text = text.strip()
    # Remove markdown code blocks
    if "```json" in text:
        text = text.split("```json")[1].split("```")[0].strip()
    elif "```" in text:
        text = text.split("```")[1].split("```")[0].strip()
    # Try direct parse first
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Try to find JSON object in text (e.g. "Here is the result: {...}")
    start = text.find("{")
    if start >= 0:
        depth, end = 0, start
        for i, c in enumerate(text[start:], start):
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    end = i
                    break
        if depth == 0:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                pass
    return None


def score_turns(segments: list[dict], model: str = "llama3.2") -> dict:
    """Score each turn via Ollama. Returns parsed JSON or fallback structure."""
    turns_text = "\n".join(
        f"Turn {i}: {s['text']}" for i, s in enumerate(segments)
    )
    if not turns_text.strip():
        return {
            "turns": [],
            "overall_summary": "No speech detected in the recording.",
        }

    resolved = _resolve_model(model)
    prompt = SCORE_USER_TEMPLATE.format(turns=turns_text)
    try:
        resp = chat(
            model=resolved,
            messages=[
                {"role": "system", "content": SCORE_SYSTEM},
                {"role": "user", "content": prompt},
            ],
            format="json",
        )
        content = _ollama_content(resp)
        parsed = _extract_json(content)
        if not parsed or "turns" not in parsed:
            logger.warning("Ollama score_turns: invalid JSON (model=%s). Response length=%d", resolved, len(content or ""))
        if parsed and "turns" in parsed:
            # Ensure turn_index and text align with segments
            for i, t in enumerate(parsed.get("turns", [])):
                t["turn_index"] = i
                if i < len(segments):
                    t["text"] = segments[i]["text"]
            return parsed
    except Exception as e:
        logger.warning("Ollama score_turns failed (model=%s): %s", resolved, e)

    return _fallback_score_structure(segments)


def _fallback_score_structure(segments: list[dict]) -> dict:
    """Minimal structure when Ollama fails or returns invalid JSON."""
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
                "actionable_feedback": "Could not score. Ensure Ollama is running and model is available.",
            }
            for i, s in enumerate(segments)
        ],
        "overall_summary": "Scoring failed. Check Ollama is running: ollama serve",
    }


def get_rewrites(
    text: str,
    model: str = "llama3.2",
    context: str = "",
    turn_index: int = 0,
    question_type: str = "Unknown",
) -> dict:
    """Get 45s and 2min better-answer versions for a job seeker."""
    if not text.strip():
        return {"tight_45s": "", "expanded_2min": ""}

    resolved = _resolve_model(model)
    prompt = REWRITE_USER_TEMPLATE.format(
        context=context or "(single answer)",
        turn_index=turn_index,
        text=text,
        question_type=question_type,
    )
    try:
        resp = chat(
            model=resolved,
            messages=[
                {"role": "system", "content": REWRITE_SYSTEM},
                {"role": "user", "content": prompt},
            ],
            format="json",
        )
        content = _ollama_content(resp)
        parsed = _extract_json(content)
        if parsed:
            return {
                "tight_45s": parsed.get("tight_45s", ""),
                "expanded_2min": parsed.get("expanded_2min", ""),
            }
    except Exception as e:
        logger.warning("Ollama get_rewrites failed: %s", e)
    return {"tight_45s": "", "expanded_2min": ""}
