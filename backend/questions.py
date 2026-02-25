"""Question bank and job-description-based question adaptation."""

import json
import logging

from llm import chat as llm_chat

logger = logging.getLogger(__name__)

DEFAULT_QUESTIONS = [
    "Tell me about yourself",
    "Why do you want this job?",
    "Why are you leaving your current role?",
    "Tell me about a challenge you overcame",
    "Describe a time you disagreed with a teammate",
    "What are your strengths and weaknesses?",
    "Where do you see yourself in 5 years?",
    "Do you have any questions for us?",
    "What's your biggest professional accomplishment?",
    "How do you handle failure or setbacks?",
]

ADAPT_SYSTEM = """You are an expert interview coach. Given a job description, generate 3-5 role-specific interview questions that hiring managers would ask for this role.

Return ONLY valid JSON with a "questions" array of strings. No markdown or extra text.
Example: {"questions": ["How have you designed APIs at scale?", "Describe your experience with distributed systems."]}"""

ADAPT_USER_TEMPLATE = """Job description:
{job_description}

Generate 3-5 role-specific interview questions. Return JSON:
{{"questions": ["question1", "question2", ...]}}"""


def _extract_json(text: str) -> dict | None:
    """Extract JSON from LLM response."""
    if not text or not text.strip():
        return None
    text = text.strip()
    if "```json" in text:
        text = text.split("```json")[1].split("```")[0].strip()
    elif "```" in text:
        text = text.split("```")[1].split("```")[0].strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
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


def adapt_questions(job_description: str, model: str = "llama3.2") -> list[str]:
    """
    Use LLM (Gemini or Ollama) to generate role-specific questions from job description.
    Returns merged list: 5-7 common + 3-5 tailored questions.
    """
    if not job_description or not job_description.strip():
        return DEFAULT_QUESTIONS.copy()

    prompt = ADAPT_USER_TEMPLATE.format(job_description=job_description.strip())

    try:
        content, _ = llm_chat(
            messages=[
                {"role": "system", "content": ADAPT_SYSTEM},
                {"role": "user", "content": prompt},
            ],
            format_json=True,
        )
        parsed = _extract_json(content) if content else None
        if parsed and "questions" in parsed:
            tailored = [q for q in parsed["questions"] if isinstance(q, str) and q.strip()]
            # Merge: 5-7 common (first half) + 3-5 tailored
            common_count = min(7, len(DEFAULT_QUESTIONS))
            tailored_count = min(5, len(tailored))
            return DEFAULT_QUESTIONS[:common_count] + tailored[:tailored_count]
    except Exception as e:
        logger.warning("adapt_questions failed: %s", e)

    return DEFAULT_QUESTIONS.copy()
