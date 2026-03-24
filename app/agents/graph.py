from langgraph.graph import StateGraph, END
from app.agents.state import InterviewState
from app.agents.parser_agent import parser_agent_node
from app.agents.memory_manager_agent import load_memory_node, save_memory_node
from app.agents.interviewer_agent import greet_candidate_node, generate_question_node
from app.agents.followup_prober_agent import followup_probe_node
from app.agents.rag_agent import rag_retriever_node
from app.agents.evaluator_agent import evaluator_node
from app.agents.report_agent import report_agent_node
from app.agents.supervisor import route_after_answer, route_after_evaluation
from app.core.logging_config import get_logger
import json, time

log = get_logger("graph")


def _safe_serialize(obj, max_len=500):
    """Serialize a value for logging, truncating long strings."""
    try:
        s = json.dumps(obj, default=str, ensure_ascii=False)
    except Exception:
        s = str(obj)
    return s[:max_len] + "..." if len(s) > max_len else s


def _logged_node(name: str, fn):
    """Wrap a node function with entry/exit logging including state and timing."""
    def wrapper(state: InterviewState) -> dict:
        log.info(f"{'='*60}")
        log.info(f"NODE ENTER: {name}")
        log.debug(f"  State keys: {list(state.keys())}")
        log.debug(f"  current_phase={state.get('current_phase')}, "
                   f"topics_covered={state.get('topics_covered')}, "
                   f"questions_asked_count={len(state.get('questions_asked', []))}, "
                   f"answers_count={len(state.get('answers', []))}, "
                   f"follow_up_count={state.get('follow_up_count', 0)}, "
                   f"interview_complete={state.get('interview_complete')}")
        if state.get("current_question"):
            log.debug(f"  current_question={state['current_question'][:150]}")
        if state.get("current_answer"):
            log.debug(f"  current_answer={state['current_answer'][:150]}")
        if state.get("current_topic"):
            log.debug(f"  current_topic={state['current_topic']}")

        start = time.time()
        try:
            result = fn(state)
        except Exception as e:
            log.error(f"NODE ERROR: {name} raised {type(e).__name__}: {e}")
            raise
        elapsed = time.time() - start

        log.info(f"NODE EXIT: {name} ({elapsed:.2f}s)")
        log.debug(f"  Return keys: {list(result.keys())}")
        for k, v in result.items():
            log.debug(f"  -> {k} = {_safe_serialize(v)}")
        log.info(f"{'='*60}")
        return result
    return wrapper


def _logged_route(name: str, fn):
    """Wrap a routing function with logging."""
    def wrapper(state: InterviewState) -> str:
        result = fn(state)
        log.info(f"ROUTE [{name}]: -> {result}")
        return result
    return wrapper


def build_interview_graph():
    log.info("Building interview graph...")
    workflow = StateGraph(InterviewState)

    # ── Register all nodes (wrapped with logging) ────────────
    workflow.add_node("parse_jd_resume",        _logged_node("parse_jd_resume", parser_agent_node))
    workflow.add_node("load_long_term_memory",  _logged_node("load_long_term_memory", load_memory_node))
    workflow.add_node("greet_candidate",        _logged_node("greet_candidate", greet_candidate_node))
    workflow.add_node("rag_retriever",          _logged_node("rag_retriever", rag_retriever_node))
    workflow.add_node("generate_question",      _logged_node("generate_question", generate_question_node))
    workflow.add_node("evaluate_answer",        _logged_node("evaluate_answer", evaluator_node))
    workflow.add_node("follow_up_probe",        _logged_node("follow_up_probe", followup_probe_node))
    workflow.add_node("end_interview",          _logged_node("end_interview", report_agent_node))
    workflow.add_node("generate_report",        _logged_node("generate_report", report_agent_node))
    workflow.add_node("save_long_term_memory",  _logged_node("save_long_term_memory", save_memory_node))

    # ── Linear entry flow ────────────────────────────────────────
    workflow.set_entry_point("parse_jd_resume")
    workflow.add_edge("parse_jd_resume",       "load_long_term_memory")
    workflow.add_edge("load_long_term_memory", "greet_candidate")
    workflow.add_edge("greet_candidate",       "rag_retriever")
    workflow.add_edge("rag_retriever",         "generate_question")

    # ── Conditional: follow-up probe OR evaluate ─────────────────
    workflow.add_conditional_edges(
        "generate_question",
        _logged_route("after_answer", route_after_answer),
        {
            "follow_up_probe": "follow_up_probe",
            "evaluate_answer": "evaluate_answer",
        }
    )
    workflow.add_edge("follow_up_probe", "evaluate_answer")

    # ── Conditional: more questions OR end ───────────────────────
    workflow.add_conditional_edges(
        "evaluate_answer",
        _logged_route("after_evaluation", route_after_evaluation),
        {
            "generate_question": "rag_retriever",
            "end_interview":     "end_interview",
        }
    )

    # ── End sequence ─────────────────────────────────────────────
    workflow.add_edge("end_interview",         "generate_report")
    workflow.add_edge("generate_report",       "save_long_term_memory")
    workflow.add_edge("save_long_term_memory", END)

    log.info("Interview graph built successfully.")
    return workflow.compile()


# Singleton compiled graph — import this in your API routes
interview_graph = build_interview_graph()
