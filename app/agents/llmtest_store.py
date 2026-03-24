"""
LLM Test Mode Store
===================
Thread-safe singleton that intercepts LLM calls during test mode.
Instead of hitting the real Ollama LLM, prompts are held here until a
tester provides a response via the /llmtest API (pages/llmtest.py).
"""

import threading
import uuid
from datetime import datetime, timezone
from langchain_core.messages import AIMessage


class LLMTestStore:
    """Holds one pending LLM prompt at a time and blocks the calling thread
    until the tester submits a response via the /llmtest endpoints."""

    def __init__(self):
        self._lock = threading.Lock()
        self.pending: dict | None = None
        self._response_event = threading.Event()
        self._response_text: str | None = None
        self.history: list = []

    # ── Called by MockLLM (runs in an agent thread) ───────────────────────
    def submit_prompt(self, agent: str, prompt: str) -> str:
        """Store a prompt and block until the tester provides a response
        (or 10-minute timeout). Returns the tester's response string."""
        prompt_id = str(uuid.uuid4())[:8]
        prompt_ts = datetime.now(timezone.utc).isoformat()

        with self._lock:
            self.pending = {
                "id": prompt_id,
                "agent": agent,
                "prompt": prompt,
                "timestamp": prompt_ts,
            }
            self._response_event.clear()
            self._response_text = None

        # Block the agent thread until response arrives or 10-min timeout
        got_response = self._response_event.wait(timeout=600)

        with self._lock:
            response = (
                self._response_text
                if got_response and self._response_text is not None
                else "[TIMEOUT – no response provided by tester]"
            )
            self.history.append({
                "id": prompt_id,
                "agent": agent,
                "prompt": prompt,
                "response": response,
                "prompt_ts": prompt_ts,
                "response_ts": datetime.now(timezone.utc).isoformat(),
            })
            self.pending = None

        return response

    # ── Called by POST /llmtest/respond (runs in FastAPI event loop) ──────
    def submit_response(self, response: str) -> bool:
        """Unblock the waiting agent thread with the tester's response.
        Returns False if there is no pending prompt to respond to."""
        with self._lock:
            if self.pending is None:
                return False
            self._response_text = response
        self._response_event.set()
        return True

    # ── Read accessors ────────────────────────────────────────────────────
    def get_pending(self) -> dict | None:
        with self._lock:
            return dict(self.pending) if self.pending else None

    def get_history(self) -> list:
        with self._lock:
            return list(self.history)

    def clear_history(self):
        with self._lock:
            self.history = []


# ── Singleton ─────────────────────────────────────────────────────────────
llmtest_store = LLMTestStore()


# ── Mock LLM ─────────────────────────────────────────────────────────────
class MockLLM:
    """Drop-in replacement for ChatOpenAI that routes prompts through the
    LLMTestStore so a human tester can provide responses."""

    def __init__(self, agent_name: str = "unknown"):
        self.agent_name = agent_name

    def invoke(self, messages) -> AIMessage:
        parts = []
        for msg in messages:
            role = type(msg).__name__.replace("Message", "")
            content = getattr(msg, "content", str(msg))
            parts.append(f"[{role}]\n{content}")
        full_prompt = "\n\n---\n\n".join(parts)

        response_text = llmtest_store.submit_prompt(self.agent_name, full_prompt)
        return AIMessage(content=response_text)


# ── LLM factory ───────────────────────────────────────────────────────────
def get_llm(state: dict, temperature: float = 0.7, agent_name: str = "unknown"):
    """Return the appropriate LLM for the given agent, or a MockLLM in test mode.

    Model is selected from settings per agent_name:
      interviewer → INTERVIEWER_MODEL
      evaluator   → EVALUATOR_MODEL
      parser      → PARSER_MODEL
      followup    → FOLLOWUP_MODEL
      report      → REPORT_MODEL

    Model name convention:
      "claude-*"  → Anthropic (ChatAnthropic)
      "gpt-*", "o1*", "o3*" → OpenAI (ChatOpenAI)
      "gemini-*"  → Google (ChatGoogleGenerativeAI)
      "ollama/*"  → Local Ollama via OpenAI-compatible API
    """
    if state.get("test_mode"):
        return MockLLM(agent_name=agent_name)

    from app.core.config import settings

    # User-supplied OpenRouter key/model overrides system defaults.
    # Strip the optional "openrouter/" provider prefix if present; the raw model
    # name is what OpenRouter's API expects.
    user_key   = state.get("user_openrouter_api_key")
    user_model = state.get("user_openrouter_model")
    if user_key and user_model:
        model_name = user_model[len("openrouter/"):] if user_model.startswith("openrouter/") else user_model
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(
            model=model_name,
            base_url=settings.OPENROUTER_BASE_URL,
            api_key=user_key,
            temperature=temperature,
            default_headers={
                "HTTP-Referer": settings.FRONTEND_URL,
                "X-Title": "InterviewIQ",
            },
        )

    model_map = {
        "interviewer": settings.INTERVIEWER_MODEL,
        "evaluator":   settings.EVALUATOR_MODEL,
        "parser":      settings.PARSER_MODEL,
        "followup":    settings.FOLLOWUP_MODEL,
        "report":      settings.REPORT_MODEL,
    }
    model = model_map.get(agent_name, settings.INTERVIEWER_MODEL)

    if model.startswith("openrouter/"):
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(
            model=model[len("openrouter/"):],
            base_url=settings.OPENROUTER_BASE_URL,
            api_key=settings.OPENROUTER_API_KEY,
            temperature=temperature,
            default_headers={
                "HTTP-Referer": settings.FRONTEND_URL,
                "X-Title": "InterviewIQ",
            },
        )
    elif model.startswith("ollama/"):
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(
            model=model[7:],
            base_url=settings.OLLAMA_BASE_URL,
            api_key="ollama",
            temperature=temperature,
        )
    elif model.startswith("claude-"):
        from langchain_anthropic import ChatAnthropic
        return ChatAnthropic(
            model=model,
            api_key=settings.ANTHROPIC_API_KEY,
            temperature=temperature,
        )
    elif model.startswith("gemini-"):
        from langchain_google_genai import ChatGoogleGenerativeAI
        return ChatGoogleGenerativeAI(
            model=model,
            google_api_key=settings.GOOGLE_API_KEY,
            temperature=temperature,
        )
    else:
        # Default: OpenAI (gpt-*, o1*, o3*, or any unknown model)
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(
            model=model,
            api_key=settings.OPENAI_API_KEY,
            temperature=temperature,
        )
