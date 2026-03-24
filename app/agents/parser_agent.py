from langchain_core.messages import HumanMessage, SystemMessage
from app.agents.state import InterviewState
from app.agents.llmtest_store import get_llm
from app.core.logging_config import get_logger
import json, re

log = get_logger("agent.parser")

PARSER_PROMPT = """Parse the provided Job Description and Resume. Extract:
1. Required technical skills (list)
2. Experience level (junior/mid/senior/lead)
3. Top 5 topic areas to cover in the interview
4. Domain (e.g., AI/ML, backend, telecom, SDE, system design)
5. Candidate's strongest matching skills

Return ONLY valid JSON with keys: skills, experience_level, topics, domain, strengths."""

def parser_agent_node(state: InterviewState) -> dict:
    log.info(f"Parsing JD ({len(state['jd_text'])} chars) and Resume ({len(state['resume_text'])} chars)")
    data_prompt = f"""JOB DESCRIPTION:
{state["jd_text"]}

RESUME:
{state["resume_text"]}"""

    log.debug(f"LLM PROMPT [parser]:\n{PARSER_PROMPT}\n\n{data_prompt}")
    response = get_llm(state, temperature=0, agent_name="parser").invoke([
        SystemMessage(content=PARSER_PROMPT),
        HumanMessage(content=data_prompt),
    ])
    raw = response.content
    log.debug(f"LLM RESPONSE [parser] ({len(raw)} chars):\n{raw}")
    try:
        match = re.search(r"```(?:json)?(.*?)```", raw, re.DOTALL)
        parsed = json.loads(match.group(1).strip() if match else raw.strip())
    except Exception as e:
        log.error(f"Failed to parse LLM response: {e}")
        parsed = {
            "skills": [], "experience_level": "mid",
            "topics": ["algorithms", "system design", "databases", "APIs", "problem solving"],
            "domain": "SDE", "strengths": []
        }
    log.info(f"Parsed profile: domain={parsed.get('domain')}, level={parsed.get('experience_level')}, "
             f"topics={parsed.get('topics')}, skills={parsed.get('skills')}, strengths={parsed.get('strengths')}")
    return {"parsed_profile": parsed, "current_phase": "loading_memory"}
