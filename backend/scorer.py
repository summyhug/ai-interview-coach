"""Score interview turns and generate rewrites. Uses Gemini (if API key set) or Ollama."""

import json
import logging

from llm import chat as llm_chat

logger = logging.getLogger(__name__)


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
- relevance_to_role: (only when job_description provided) Did the answer connect to the job's requirements? (true/false, note: string)
- actionable_feedback: 1-2 sentences on how to improve this answer as a job seeker (interview quality, not speech). When job description provided, suggest how to better tailor the answer to the role."""

SCORE_USER_TEMPLATE = """Score these interview answer turns. Each turn is a candidate's spoken response (interviewer questions not included).
{question_context}
{job_context}

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
      "relevance_to_role": {{ "met": true/false, "note": "..." }},
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


def score_turns(
    segments: list[dict],
    model: str = "llama3.2",
    question_text: str | None = None,
    job_description: str | None = None,
) -> dict:
    """Score each turn via Ollama. Returns parsed JSON or fallback structure."""
    turns_text = "\n".join(
        f"Turn {i}: {s['text']}" for i, s in enumerate(segments)
    )
    if not turns_text.strip():
        return {
            "turns": [],
            "overall_summary": "No speech detected in the recording.",
        }

    question_context = ""
    if question_text and question_text.strip():
        question_context = f"The interviewer asked: {question_text.strip()}\n"

    job_context = ""
    if job_description and job_description.strip():
        job_context = f"""The candidate is applying for this role. Job description:
{job_description.strip()}

Evaluate whether their answer is relevant and tailored to this opportunity.
- For "Tell me about yourself": Did they highlight experience aligned with the role? (e.g., API role → mention API/backend work)
- For "Why do you want this job?": Strong JD relevance expected
- actionable_feedback should suggest how to better tailor the answer to this role when applicable

"""

    prompt = SCORE_USER_TEMPLATE.format(
        turns=turns_text,
        question_context=question_context,
        job_context=job_context,
    )
    try:
        content, provider = llm_chat(
            messages=[
                {"role": "system", "content": SCORE_SYSTEM},
                {"role": "user", "content": prompt},
            ],
            format_json=True,
        )
        parsed = _extract_json(content) if content else None
        if not parsed or "turns" not in parsed:
            logger.warning("LLM score_turns: invalid JSON (provider=%s). Response length=%d", provider, len(content or ""))
        if parsed and "turns" in parsed:
            # Ensure turn_index and text align with segments; add relevance_to_role if missing
            for i, t in enumerate(parsed.get("turns", [])):
                t["turn_index"] = i
                if i < len(segments):
                    t["text"] = segments[i]["text"]
                if "relevance_to_role" not in t and job_description:
                    t["relevance_to_role"] = {"met": None, "note": ""}
            return parsed
    except Exception as e:
        logger.warning("LLM score_turns failed: %s", e)

    return _fallback_score_structure(segments, job_description)


def compute_pace(segments: list[dict]) -> list[dict]:
    """
    Compute words-per-minute (WPM) for each segment.
    Returns list of {pace_wpm, pace_rating, pace_feedback} per segment.
    """
    result = []
    for seg in segments:
        text = seg.get("text", "") or ""
        start = seg.get("start", 0)
        end = seg.get("end", start + 1)
        duration_sec = max(0.01, end - start)
        word_count = len(text.split())
        wpm = (word_count / duration_sec) * 60 if duration_sec > 0 else 0

        if wpm > 180:
            rating, feedback = "too_fast", "You're speaking too quickly. Slowing down will help the interviewer follow your points."
        elif wpm > 160:
            rating, feedback = "slightly_fast", "Pace is a bit quick. Consider slowing slightly for clarity."
        elif 100 <= wpm <= 160:
            rating, feedback = "good", "Speaking pace is good."
        elif wpm >= 80:
            rating, feedback = "slightly_slow", "Pace could be a bit quicker to maintain engagement."
        else:
            rating, feedback = "too_slow", "Speaking pace is slow. Try to maintain a more conversational rhythm."

        result.append({"pace_wpm": round(wpm, 1), "pace_rating": rating, "pace_feedback": feedback})
    return result


def _fallback_score_structure(segments: list[dict], job_description: str | None = None) -> dict:
    """Minimal structure when Ollama fails or returns invalid JSON."""
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
            "actionable_feedback": "Could not score. Set GEMINI_API_KEY for cloud LLM or ensure Ollama is running: ollama serve",
        }
        if job_description:
            t["relevance_to_role"] = {"met": None, "note": ""}
        turns.append(t)
    return {
        "turns": turns,
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

    prompt = REWRITE_USER_TEMPLATE.format(
        context=context or "(single answer)",
        turn_index=turn_index,
        text=text,
        question_type=question_type,
    )
    try:
        content, _ = llm_chat(
            messages=[
                {"role": "system", "content": REWRITE_SYSTEM},
                {"role": "user", "content": prompt},
            ],
            format_json=True,
        )
        parsed = _extract_json(content) if content else None
        if parsed:
            return {
                "tight_45s": parsed.get("tight_45s", ""),
                "expanded_2min": parsed.get("expanded_2min", ""),
            }
    except Exception as e:
        logger.warning("LLM get_rewrites failed: %s", e)
    return {"tight_45s": "", "expanded_2min": ""}
