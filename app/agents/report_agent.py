from langchain_core.messages import HumanMessage
from app.agents.state import InterviewState
from app.agents.llmtest_store import get_llm
from app.core.logging_config import get_logger
import json, re

log = get_logger("agent.report")

REPORT_PROMPT = """You are a professional interview coach. Compile a comprehensive scorecard from the interview data.
Return ONLY valid JSON (no markdown) with this exact structure:
{
  "aggregate_score": <0-100>,
  "level_benchmark": "Junior|Mid|Senior|Lead",
  "per_question_breakdown": [{"topic": "...", "score": <0-100>, "feedback": "..."}],
  "topic_gaps": ["list of weak topic areas"],
  "strengths": ["list of demonstrated strengths"],
  "improvement_suggestions": ["specific actionable suggestions per weak area"],
  "summary": "<3-4 sentence overall assessment>"
}"""

def report_agent_node(state: InterviewState) -> dict:
    answers = state.get("answers", [])
    scores = state.get("scores", [])
    profile = state.get("parsed_profile", {})
    practice = state.get("practice_mode", False)

    log.info(f"Generating report: {len(answers)} answers, {len(scores)} scores, practice={practice}")

    qa_data = json.dumps([{
        "q": a.get("question", ""),
        "a": (a.get("answer") or "")[:300],
        "topic": a.get("topic", ""),
        "scores": a.get("scores", {})
    } for a in answers if a.get("question")], indent=2)

    prompt = f"""{REPORT_PROMPT}

Candidate profile: {json.dumps(profile)}
Mode: {"Practice" if practice else "Assessment"}
Interview Q&A with scores:
{qa_data}"""

    log.debug(f"LLM PROMPT [report] ({len(prompt)} chars):\n{prompt[:1000]}{'...' if len(prompt) > 1000 else ''}")
    response = get_llm(state, temperature=0.2, agent_name="report").invoke([HumanMessage(content=prompt)])
    raw = response.content
    log.debug(f"LLM RESPONSE [report] ({len(raw)} chars):\n{raw}")
    try:
        match = re.search(r"```(?:json)?(.*?)```", raw, re.DOTALL)
        report = json.loads(match.group(1).strip() if match else raw.strip())
        log.debug(f"Parsed report keys: {list(report.keys())}")
    except Exception as e:
        log.error(f"Failed to parse report JSON: {e}")
        total = sum(s.get("overall_weighted", 50) for s in scores)
        avg = total // max(len(scores), 1)
        report = {
            "aggregate_score": avg,
            "level_benchmark": str(profile.get("experience_level", "mid")).capitalize(),
            "per_question_breakdown": [],
            "topic_gaps": [],
            "strengths": profile.get("strengths", []),
            "improvement_suggestions": ["Continue practicing mock technical interviews."],
            "summary": f"Candidate scored {avg}/100 overall across {len(answers)} questions."
        }
    log.info(f"Report generated: aggregate_score={report.get('aggregate_score')}, level={report.get('level_benchmark')}")
    return {"final_report": report, "interview_complete": True}
