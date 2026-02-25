"""LLM abstraction: Gemini (when API key set) as primary, Ollama as fallback."""

import json
import logging
import os

logger = logging.getLogger(__name__)

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
LLM_PROVIDER = os.environ.get("LLM_PROVIDER", "auto")  # "auto" | "gemini" | "ollama"


def _gemini_chat(messages: list[dict], format_json: bool = False) -> str | None:
    """Call Gemini API. Returns content string or None on failure."""
    if not GEMINI_API_KEY:
        return None
    try:
        import google.generativeai as genai
        genai.configure(api_key=GEMINI_API_KEY)
        system = next((m["content"] for m in messages if m.get("role") == "system"), None)
        user_content = next((m["content"] for m in messages if m.get("role") == "user"), "")
        model = genai.GenerativeModel(
            "gemini-1.5-flash",
            system_instruction=system if system else "You are a helpful assistant.",
        )
        response = model.generate_content(user_content)
        text = response.text if hasattr(response, "text") else str(response)
        if format_json and text:
            if "```json" in text:
                text = text.split("```json")[1].split("```")[0].strip()
            elif "```" in text:
                text = text.split("```")[1].split("```")[0].strip()
        return text or None
    except ImportError:
        logger.debug("google-generativeai not installed, skipping Gemini")
        return None
    except Exception as e:
        logger.warning("Gemini API failed: %s", e)
        return None


def _ollama_chat(messages: list[dict], format_json: bool = False) -> str | None:
    """Call Ollama. Returns content string or None on failure."""
    try:
        from ollama import chat as ollama_chat
        # Ollama format: system + user
        system_content = next((m["content"] for m in messages if m.get("role") == "system"), None)
        user_content = next((m["content"] for m in messages if m.get("role") == "user"), "")
        msgs = []
        if system_content:
            msgs.append({"role": "system", "content": system_content})
        msgs.append({"role": "user", "content": user_content})
        resp = ollama_chat(model=os.environ.get("OLLAMA_MODEL", "llama3.2"), messages=msgs, format="json" if format_json else None)
        msg = getattr(resp, "message", None) or (resp.get("message") if hasattr(resp, "get") else None)
        if not msg:
            return None
        content = getattr(msg, "content", "") if not isinstance(msg, dict) else msg.get("content", "")
        return content or None
    except ImportError:
        logger.debug("ollama not installed")
        return None
    except Exception as e:
        logger.warning("Ollama failed: %s", e)
        return None


def chat(messages: list[dict], format_json: bool = False) -> tuple[str | None, str]:
    """
    Call LLM. Tries Gemini first (if API key set), then Ollama.
    Returns (content, provider) where provider is "gemini" or "ollama".
    """
    use_gemini = LLM_PROVIDER in ("gemini", "auto") and GEMINI_API_KEY
    use_ollama = LLM_PROVIDER in ("ollama", "auto")

    if use_gemini:
        content = _gemini_chat(messages, format_json)
        if content:
            return content, "gemini"

    if use_ollama:
        content = _ollama_chat(messages, format_json)
        if content:
            return content, "ollama"

    return None, "none"
