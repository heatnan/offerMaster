"""LLM client — OpenAI 兼容协议。"""
import json
from openai import OpenAI

from ..config import settings


_client: OpenAI | None = None


def get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(
            base_url=settings.OPENAI_BASE_URL,
            api_key=settings.OPENAI_API_KEY,
        )
    return _client


def chat(messages: list[dict], temperature: float = 0.7, response_format: str | None = None) -> str:
    """Basic chat completion. Returns text content."""
    kwargs = {
        "model": settings.LLM_MODEL,
        "messages": messages,
        "temperature": temperature,
    }
    if response_format == "json":
        kwargs["response_format"] = {"type": "json_object"}
    resp = get_client().chat.completions.create(**kwargs)
    return resp.choices[0].message.content or ""


def chat_json(messages: list[dict], temperature: float = 0.3) -> dict:
    """Chat expecting JSON output. Robust to code-fence wrappers."""
    text = chat(messages, temperature=temperature, response_format="json")
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Fallback: try to extract first {...} block
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(text[start : end + 1])
        raise
