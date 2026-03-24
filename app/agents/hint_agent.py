from langchain_core.messages import HumanMessage, SystemMessage
from app.agents.state import InterviewState
from app.agents.llmtest_store import get_llm
from app.core.logging_config import get_logger

log = get_logger("agent.hint")

HINT_SYSTEM = """You are a supportive interview coach helping a candidate during a practice session.
The candidate has asked for a hint. Your job is to give a 2-3 sentence nudge that:
- Points them toward the right APPROACH without giving the answer away
- Mentions the key concept or technique they should think about
- Encourages them to go deeper

Do NOT give the full answer. Be concise and encouraging."""


def generate_hint_node(state: InterviewState) -> dict:
    question = state.get("current_question", "")
    topic    = state.get("current_topic", "")
    partial  = (state.get("current_answer") or "").strip()

    log.info(f"Generating hint: topic={topic}, partial_len={len(partial)}")

    user_msg = f"""Interview question: {question}
Topic: {topic}"""
    if partial:
        user_msg += f"\nCandidate's partial answer so far: {partial}"

    user_msg += "\n\nGive a 2-3 sentence hint that guides the approach without revealing the answer."

    try:
        response = get_llm(state, temperature=0.4, agent_name="interviewer").invoke([
            SystemMessage(content=HINT_SYSTEM),
            HumanMessage(content=user_msg),
        ])
        hint = response.content.strip()
        log.info(f"Hint generated ({len(hint)} chars)")
    except Exception as exc:
        log.error(f"Hint generation failed: {exc}")
        hint = f"Think about the core concept behind '{topic}'. Consider what trade-offs are involved and how you'd structure your approach step-by-step."

    return {"hint_text": hint}
