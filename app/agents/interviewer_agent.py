from langchain_core.messages import HumanMessage, SystemMessage
from app.agents.state import InterviewState
from app.agents.llmtest_store import get_llm
from app.core.logging_config import get_logger

log = get_logger("agent.interviewer")

INTERVIEWER_NAME = "Kate"

INTERVIEW_SYSTEM = f"""You are {INTERVIEWER_NAME}, a senior technical interviewer at a top-tier tech company.
Conduct a professional, realistic technical interview. Be conversational but rigorous.
Ask ONE question at a time. Tailor questions to the candidate's experience level and the job requirements.
Never repeat questions already asked in this session.

STRICT ROLE RULES:
- Your name is {INTERVIEWER_NAME}. Use it when introducing yourself.
- You are the INTERVIEWER. You are speaking DIRECTLY to the CANDIDATE.
- Never assume you are talking to a developer, colleague, or system builder.
- Never comment on or praise the candidate's answer before asking the next question.
- Never ask the candidate what topic they want to cover or what they want to do next.
- Do NOT use "we" in a collaborative sense about any system the candidate describes.
- When generating a question, return ONLY the question text with no preamble."""

# Fallback topics when all primary topics exhausted
EXTRA_TOPICS = [
    "code quality & best practices", "testing strategies", "debugging techniques",
    "scalability", "concurrency & parallelism", "security fundamentals",
    "performance optimization", "design patterns", "CI/CD pipelines",
]

def greet_candidate_node(state: InterviewState) -> dict:
    profile = state.get("parsed_profile", {})
    domain = profile.get("domain", "software engineering")
    level = profile.get("experience_level", "mid")
    candidate_name = profile.get("candidate_name", "").strip() or "there"
    context = state.get("long_term_context", "")
    prior_note = " I can see you've practiced with us before — I'll make sure to cover fresh ground." \
        if context and "No prior" not in context else ""

    log.info(f"Greeting candidate: name={candidate_name}, domain={domain}, level={level}, has_prior_context={bool(context)}")
    greeting_prompt = f"""Write a warm, professional interview opening greeting.

Interviewer name: {INTERVIEWER_NAME}
Candidate name: {candidate_name}
Role domain: {level}-level {domain}{prior_note}

Rules:
- Address the candidate by their name ({candidate_name}) and introduce yourself as {INTERVIEWER_NAME}.
- Keep it to 2-3 sentences.
- Do NOT use placeholder text like [Name] or [Your Name] — use the actual names provided above.
- Do NOT ask any technical question yet."""
    log.debug(f"LLM PROMPT [greeting]:\n  system: {INTERVIEW_SYSTEM[:200]}...\n  human: {greeting_prompt}")
    greeting = get_llm(state, temperature=0.7, agent_name="interviewer-greeting").invoke([
        SystemMessage(content=INTERVIEW_SYSTEM),
        HumanMessage(content=greeting_prompt)
    ])
    log.debug(f"LLM RESPONSE [greeting] ({len(greeting.content)} chars):\n{greeting.content}")
    return {
        "current_phase": "technical",
        "answers": [{"role": "interviewer", "content": greeting.content, "topic": "greeting"}]
    }

_BAD_QUESTION_STARTS = (
    "nice", "great", "good", "that's", "this is", "well done", "excellent",
    "perfect", "sounds good", "awesome", "interesting", "i see", "i think",
    "do you want", "would you like",
)

def _is_valid_question(text: str) -> bool:
    """Reject responses that break interviewer role or are not actual questions."""
    stripped = text.strip()
    if not stripped.endswith("?"):
        return False
    if any(stripped.lower().startswith(b) for b in _BAD_QUESTION_STARTS):
        return False
    return True

def _is_similar(q1: str, q2: str) -> bool:
    """Overlap check — if 45%+ of words match, treat as duplicate."""
    words1 = set(q1.lower().split())
    words2 = set(q2.lower().split())
    if not words1 or not words2:
        return False
    overlap = len(words1 & words2) / min(len(words1), len(words2))
    return overlap > 0.45

def generate_question_node(state: InterviewState) -> dict:
    profile = state.get("parsed_profile", {}) or {}
    topics = profile.get("topics", ["general programming"])
    asked = state.get("questions_asked", [])
    covered = state.get("topics_covered", [])
    level = profile.get("experience_level", "mid")

    # De-duplicate covered list for accurate filtering
    covered_set = set(covered)
    log.info(f"Question gen: topics={topics}, covered={covered_set}, asked_count={len(asked)}")

    # Pick next uncovered topic from primary list, then fallback list
    remaining = [t for t in topics if t not in covered_set]
    if not remaining:
        remaining = [t for t in EXTRA_TOPICS if t not in covered_set]
        log.info(f"Primary topics exhausted, using fallback: {remaining[:3]}")
    if not remaining:
        # Absolute fallback — cycle with a suffix to force novelty
        remaining = [f"{topics[i % len(topics)]} (advanced)" for i in range(len(covered), len(covered) + 3)]
        log.info(f"All topics exhausted, using advanced suffix: {remaining[:3]}")

    next_topic = remaining[0]
    log.info(f"Selected next topic: {next_topic}")

    # Build full list of previously asked questions so the LLM can avoid them
    asked_numbered = "\n".join(f"  {i+1}. {q}" for i, q in enumerate(asked)) if asked else "None yet"
    covered_list = ", ".join(covered_set) if covered_set else "None yet"

    prompt = f"""Generate a {level}-level technical interview question about: {next_topic}
Skills to test: {", ".join(profile.get("skills", [])[:5])}
Prior session context (avoid repetition): {state.get("long_term_context", "")[:400]}

Topics ALREADY COVERED (do NOT ask about these again):
{covered_list}

Questions ALREADY ASKED this session (DO NOT repeat or rephrase ANY of these):
{asked_numbered}

RAG context for reference: {state.get("rag_context", "")[:400]}

Rules:
- Ask exactly ONE question that is COMPLETELY DIFFERENT from all previously asked questions
- The question MUST focus specifically on {next_topic}
- Do NOT rephrase or reword any previous question
- Return only the question text, no preamble"""

    # Try up to 3 times to get a non-duplicate question
    log.debug(f"LLM PROMPT [question_gen]:\n{prompt}")
    question = ""
    _llm = get_llm(state, temperature=0.7, agent_name="interviewer-question")
    for attempt in range(3):
        log.info(f"Question generation attempt {attempt+1}/3 for topic '{next_topic}'")
        response = _llm.invoke([SystemMessage(content=INTERVIEW_SYSTEM), HumanMessage(content=prompt)])
        candidate_q = response.content.strip()
        log.debug(f"LLM RESPONSE [question_gen] attempt {attempt+1} ({len(candidate_q)} chars):\n{candidate_q}")

        # Reject if the LLM broke character (compliment/meta-commentary)
        if not _is_valid_question(candidate_q):
            log.warning(f"Question attempt {attempt+1} failed role validation (bad start or missing '?'), retrying...")
            prompt += f"\n\nYour previous response was not a valid interview question. Return ONLY a question ending with '?'."
            continue

        # Check for similarity with previously asked questions
        is_dup = False
        for prev in asked:
            if _is_similar(candidate_q, prev):
                log.debug(f"  Similar to previous: {prev[:80]}...")
                is_dup = True
                break
        if not is_dup:
            question = candidate_q
            log.info(f"Question accepted on attempt {attempt+1}: {question[:100]}")
            break
        log.warning(f"Duplicate detected on attempt {attempt+1}, retrying...")
        # On retry, add explicit instruction
        prompt += f"\n\nYour previous attempt was too similar to an existing question. Generate a COMPLETELY DIFFERENT question about {next_topic}."

    if not question:
        # Use last attempt even if similar
        question = response.content.strip()
        log.warning(f"Using last attempt despite possible similarity: {question[:100]}")

    return {
        "current_question": question,
        "current_topic": next_topic,
        "questions_asked": [question],
        "topics_covered": [next_topic],
        "follow_up_count": 0,
        "follow_up_needed": False,
    }

