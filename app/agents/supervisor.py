from app.agents.state import InterviewState
from app.core.logging_config import get_logger

log = get_logger("agent.supervisor")

def supervisor_node(state: InterviewState) -> dict:
    phase = state.get("current_phase", "init")
    if phase == "init":
        return {"current_phase": "parsing"}
    if phase == "parsing":
        return {"current_phase": "loading_memory"}
    if phase == "loading_memory":
        return {"current_phase": "greeting"}
    if phase == "greeting":
        return {"current_phase": "technical"}
    if state.get("interview_complete"):
        return {"current_phase": "reporting"}
    if phase == "reporting":
        return {"current_phase": "saving_memory"}
    return {}

def route_after_answer(state: InterviewState) -> str:
    """Conditional edge: follow-up probe or straight to evaluation."""
    follow_up = state.get("follow_up_needed") and state.get("follow_up_count", 0) < 2
    route = "follow_up_probe" if follow_up else "evaluate_answer"
    log.info(f"Route after answer: {route} (follow_up_needed={state.get('follow_up_needed')}, count={state.get('follow_up_count', 0)})")
    return route

def route_after_evaluation(state: InterviewState) -> str:
    """Conditional edge: more questions or end interview."""
    topics = state.get("topics_covered", [])
    unique_topics = set(topics) - {"greeting"}  # Don't count greeting
    complete = state.get("interview_complete", False)
    route = "end_interview" if len(unique_topics) >= 5 or complete else "generate_question"
    log.info(f"Route after eval: {route} (unique_topics={len(unique_topics)}, complete={complete})")
    return route
