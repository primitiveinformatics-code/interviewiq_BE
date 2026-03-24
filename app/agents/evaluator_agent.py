from langchain_core.messages import HumanMessage, SystemMessage
from app.agents.state import InterviewState
from app.agents.llmtest_store import get_llm
from app.core.logging_config import get_logger
import json, re

log = get_logger("agent.evaluator")

EVAL_SYSTEM = """You are an expert technical interview evaluator. Score the candidate's answer on 5 dimensions.
Return ONLY a valid JSON object (no markdown):
{
  "technical_accuracy": <0-10>,
  "depth": <0-10>,
  "problem_solving": <0-10>,
  "communication": <0-10>,
  "confidence": <0-10>,
  "overall_weighted": <0-100>,
  "feedback": "<1-2 sentence constructive feedback>",
  "needs_follow_up": <true|false>
}
Scoring weights: technical_accuracy=30%, depth=25%, problem_solving=20%, communication=15%, confidence=10%.
In practice mode, apply a -20% penalty to overall_weighted."""

def evaluator_node(state: InterviewState) -> dict:
    question = state.get("current_question", "")
    answer = state.get("current_answer", "No answer provided.")
    topic = state.get("current_topic", "")
    level = (state.get("parsed_profile") or {}).get("experience_level", "mid")
    practice_mode = state.get("practice_mode", False)

    log.info(f"Evaluating answer: topic={topic}, level={level}, practice={practice_mode}")
    log.debug(f"Question: {question[:100]}")
    log.debug(f"Answer: {answer[:100]}")

    prompt = f"""Question: {question}
Topic: {topic}
Expected level: {level}
Practice mode: {practice_mode}
Candidate answer: {answer}

Evaluate and return the JSON score object."""

    log.debug(f"LLM PROMPT [evaluator]:\n  system: {EVAL_SYSTEM[:200]}...\n  human: {prompt}")
    response = get_llm(state, temperature=0, agent_name="evaluator").invoke([
        SystemMessage(content=EVAL_SYSTEM),
        HumanMessage(content=prompt),
    ])
    raw = response.content
    log.debug(f"LLM RESPONSE [evaluator] ({len(raw)} chars):\n{raw}")
    try:
        match = re.search(r"```(?:json)?(.*?)```", raw, re.DOTALL)
        score_data = json.loads(match.group(1).strip() if match else raw.strip())
        log.debug(f"Parsed score data: {json.dumps(score_data, default=str)}")
    except Exception as e:
        log.error(f"Failed to parse evaluator JSON: {e}\n  Raw response: {raw[:300]}")
        score_data = {
            "technical_accuracy": 5, "depth": 5, "problem_solving": 5,
            "communication": 5, "confidence": 5, "overall_weighted": 50,
            "feedback": "Evaluation could not be parsed.", "needs_follow_up": False
        }

    if practice_mode:
        score_data["overall_weighted"] = round(score_data.get("overall_weighted", 50) * 0.8)

    needs_follow_up = score_data.pop("needs_follow_up", False)
    scored_qa = {"question": question, "answer": answer, "topic": topic, "scores": score_data}

    log.info(f"Evaluation result: overall={score_data.get('overall_weighted')}, follow_up={needs_follow_up}, "
             f"tech_accuracy={score_data.get('technical_accuracy')}, depth={score_data.get('depth')}, "
             f"problem_solving={score_data.get('problem_solving')}, communication={score_data.get('communication')}, "
             f"confidence={score_data.get('confidence')}")
    log.debug(f"Feedback: {score_data.get('feedback', '')}")
    log.info(f"Follow-up decision: needs_follow_up={needs_follow_up}, "
             f"follow_up_count={state.get('follow_up_count', 0)}, "
             f"will_follow_up={needs_follow_up and state.get('follow_up_count', 0) < 2}")
    return {
        "scores": [score_data],
        "answers": [scored_qa],
        "follow_up_needed": needs_follow_up and state.get("follow_up_count", 0) < 2,
    }
