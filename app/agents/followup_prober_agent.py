from langchain_core.messages import HumanMessage, SystemMessage
from app.agents.state import InterviewState
from app.agents.llmtest_store import get_llm
from app.core.logging_config import get_logger

log = get_logger("agent.followup")

PROBER_SYSTEM = """You are a senior technical interviewer conducting a live job interview.
You are speaking DIRECTLY to the candidate sitting in front of you.
Your ONLY job: generate exactly ONE follow-up interview question to probe their technical understanding deeper.

STRICT OUTPUT RULES:
- Output ONLY the question text. Nothing else.
- The question MUST end with '?'
- Do NOT praise or compliment the candidate (no "Nice", "Great", "Good answer", "That's well done")
- Do NOT comment on or evaluate the quality of their answer
- Do NOT talk about the system, codebase, or project being discussed as if you are a collaborator or developer on it
- Do NOT ask the candidate what they "want to do next" or offer choices about the interview direction
- Do NOT use "we" in a collaborative sense — you are the INTERVIEWER, they are the CANDIDATE
- Stay strictly in the interviewer role at all times"""

_BAD_STARTS = (
    "nice", "great", "good", "that's", "this is", "well done", "excellent",
    "perfect", "i see", "i think", "do you want", "would you like", "we could",
    "sounds good", "awesome", "interesting", "cool", "i'd say",
)

def _is_valid_followup(text: str) -> bool:
    """Reject responses that are meta-commentary or developer talk instead of interview questions."""
    stripped = text.strip()
    if not stripped.endswith("?"):
        return False
    if any(stripped.lower().startswith(b) for b in _BAD_STARTS):
        return False
    return True

def followup_probe_node(state: InterviewState) -> dict:
    question = state.get("current_question", "")
    answer = state.get("current_answer", "")
    topic = state.get("current_topic", "")
    count = state.get("follow_up_count", 0)

    log.info(f"Follow-up probe #{count+1}: topic={topic}")
    log.debug(f"Original Q: {question[:80]}")
    log.debug(f"Candidate A: {answer[:80]}")

    prompt = f"""Original question: {question}
Topic: {topic}
Candidate answered: {answer}
This is follow-up attempt #{count + 1}. Generate a deeper technical follow-up question."""

    log.debug(f"LLM PROMPT [followup]:\n  system: {PROBER_SYSTEM}\n  human: {prompt}")
    llm = get_llm(state, temperature=0.2, agent_name="followup")
    follow_up = ""
    for attempt in range(1, 4):
        response = llm.invoke([
            SystemMessage(content=PROBER_SYSTEM),
            HumanMessage(content=prompt),
        ])
        candidate = response.content.strip()
        log.debug(f"LLM RESPONSE [followup] attempt {attempt} ({len(candidate)} chars):\n{candidate}")
        if _is_valid_followup(candidate):
            follow_up = candidate
            break
        log.warning(f"Follow-up attempt {attempt} failed validation (bad start or missing '?'), retrying...")

    if not follow_up:
        follow_up = f"Can you walk me through a concrete production example of that approach for {topic}?"
        log.warning(f"All attempts failed validation, using safe fallback: {follow_up}")

    log.info(f"Follow-up question generated: {follow_up[:100]}")
    return {
        "current_question": follow_up,
        "follow_up_count": count + 1,
        "questions_asked": [follow_up],
        "follow_up_needed": False,
    }
