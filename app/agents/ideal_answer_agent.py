from langchain_core.messages import HumanMessage, SystemMessage
from app.agents.llmtest_store import get_llm
from app.core.logging_config import get_logger

log = get_logger("agent.ideal_answer")

IDEAL_ANSWER_SYSTEM = """You are a senior technical interviewer and mentor with deep expertise across software engineering domains.
When given an interview question, generate a concise, high-quality model answer that a strong candidate would give.

RULES:
- Write the answer as if you are the candidate speaking (first person).
- Cover the key concepts clearly and accurately.
- Match the depth to the experience level specified.
- Include a concrete example or analogy where it adds clarity.
- Keep the answer focused: 150-250 words. No waffle.
- Do NOT mention the question itself at the start — go straight into the answer."""


def generate_ideal_answer(
    question: str,
    topic: str,
    experience_level: str = "mid",
    state: dict | None = None,
) -> str:
    """Generate a model/ideal answer for the given interview question.

    Args:
        question: The interview question text.
        topic: The topic/domain of the question.
        experience_level: "junior", "mid", or "senior".
        state: Optional interview state dict (used to select real vs mock LLM).

    Returns:
        The ideal answer as a plain string.
    """
    state = state or {}
    prompt = f"""Interview question: {question}
Topic: {topic}
Expected experience level: {experience_level}

Generate the ideal model answer a strong {experience_level}-level candidate would give."""

    log.info(f"Generating ideal answer: topic={topic}, level={experience_level}")
    log.debug(f"Question: {question[:120]}")

    llm = get_llm(state, temperature=0.3, agent_name="ideal-answer")
    response = llm.invoke([
        SystemMessage(content=IDEAL_ANSWER_SYSTEM),
        HumanMessage(content=prompt),
    ])
    answer = response.content.strip()
    log.debug(f"Ideal answer ({len(answer)} chars): {answer[:120]}")
    return answer
