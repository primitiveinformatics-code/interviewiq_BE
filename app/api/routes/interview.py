from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select
import asyncio
import os
import uuid, json, time, pickle
from datetime import datetime
import redis.asyncio as aioredis
from app.agents.state import InterviewState
from app.agents.parser_agent import parser_agent_node
from app.agents.memory_manager_agent import load_memory_node, save_memory_node
from app.agents.interviewer_agent import greet_candidate_node, generate_question_node
from app.agents.followup_prober_agent import followup_probe_node
from app.agents.rag_agent import rag_retriever_node
from app.agents.evaluator_agent import evaluator_node
from app.agents.report_agent import report_agent_node
from app.agents.supervisor import route_after_answer, route_after_evaluation
from app.db.database import SessionLocal
from app.db.models import Document, DocType, Session as InterviewSession, InterviewQA, User
from app.core.security import get_current_user, verify_token
from app.core.config import settings
from app.core.logging_config import get_logger
from app.core.crypto import decrypt_content

router = APIRouter()
log = get_logger("api.interview")

# ── Redis state helpers ───────────────────────────────────────────────────────
# State is stored in Redis instead of an in-memory dict so it survives backend
# restarts and works across horizontally scaled instances.
STATE_TTL = 3600  # 1 hour — abandoned sessions are auto-cleaned

# Module-level singleton: initialized on first call, reused across warm Lambda
# invocations and across requests on EC2. Avoids importing from app.main so
# this module works correctly in Lambda (where lifespan never runs).
_redis_client: aioredis.Redis | None = None


async def _get_redis() -> aioredis.Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = aioredis.from_url(
            settings.REDIS_URL,
            decode_responses=False,
            socket_keepalive=True,
            socket_connect_timeout=10,
        )
    return _redis_client


async def _get_state(session_id: str) -> dict | None:
    redis = await _get_redis()
    data = await redis.get(f"interview:{session_id}")
    return pickle.loads(data) if data else None


async def _set_state(session_id: str, state: dict) -> None:
    redis = await _get_redis()
    await redis.set(f"interview:{session_id}", pickle.dumps(state), ex=STATE_TTL)


async def _del_state(session_id: str) -> None:
    redis = await _get_redis()
    await redis.delete(f"interview:{session_id}")


# ── Node helpers ──────────────────────────────────────────────────────────────

def _merge_state(state: dict, updates: dict) -> None:
    ADD_FIELDS = {"topics_covered", "questions_asked", "answers", "scores"}
    for key, value in updates.items():
        if key in ADD_FIELDS and isinstance(value, list):
            state.setdefault(key, []).extend(value)
        else:
            state[key] = value


def _run_node(name: str, fn, state: dict) -> dict:
    log.info(f"{'='*60}")
    log.info(f"NODE ENTER: {name}")
    log.debug(
        f"  current_phase={state.get('current_phase')}, "
        f"topics_covered={state.get('topics_covered')}, "
        f"questions_asked_count={len(state.get('questions_asked', []))}, "
        f"answers_count={len(state.get('answers', []))}, "
        f"follow_up_count={state.get('follow_up_count', 0)}, "
        f"interview_complete={state.get('interview_complete')}"
    )
    start = time.time()
    result = fn(state)
    elapsed = time.time() - start
    log.info(f"NODE EXIT: {name} ({elapsed:.2f}s)")
    log.debug(f"  Return keys: {list(result.keys())}")
    for k, v in result.items():
        s = json.dumps(v, default=str, ensure_ascii=False)
        log.debug(f"  -> {k} = {s[:300]}{'...' if len(s) > 300 else ''}")
    log.info(f"{'='*60}")
    _merge_state(state, result)
    return result


async def _run_node_async(name: str, fn, state: dict) -> dict:
    return await asyncio.to_thread(_run_node, name, fn, state)


def _persist_completed_session(session_id: str, state: dict, db) -> None:
    """Save scored QA pairs to interview_qa and mark the session completed."""
    saved = 0
    for qa in state.get("answers", []):
        if qa.get("question") and qa.get("scores"):
            db.add(InterviewQA(
                session_id=uuid.UUID(session_id),
                question=qa["question"],
                answer=qa.get("answer", ""),
                topic=qa.get("topic", ""),
                scores=qa.get("scores"),
            ))
            saved += 1
    sess = db.execute(
        select(InterviewSession)
        .where(InterviewSession.session_id == uuid.UUID(session_id))
    ).scalar_one_or_none()
    if sess:
        sess.status = "completed"
        sess.ended_at = datetime.utcnow()
    try:
        db.commit()
        log.info(f"Persisted {saved} QAs + marked session completed: {session_id}")
    except Exception as exc:
        log.error(f"DB persist failed for {session_id}: {exc}")
        db.rollback()


# ── WebSocket endpoint ────────────────────────────────────────────────────────

class AnswerRequest(BaseModel):
    answer: str


@router.websocket("/{session_id}")
async def interview_websocket(
    websocket: WebSocket,
    session_id: str,
    token: str = Query(...),
):
    """
    Real-time interview over WebSocket.
    State is persisted in Redis — survives backend restarts and scales horizontally.

    session_type "trial"  → stops after 3 scored answers, sends {type: "trial_limit"}
    session_type "full"   → runs until ≥5 topics covered (normal completion)
    session_type "testing"→ uses MockLLM (admin test mode)
    """
    await websocket.accept()

    # ── Authenticate ─────────────────────────────────────────────────────────
    try:
        token_payload = verify_token(token)
        requesting_user_id = token_payload.get("sub")
        if not requesting_user_id:
            raise ValueError("No sub claim in token")
    except Exception:
        log.warning(f"WS REJECTED (unauthenticated): session_id={session_id}")
        await websocket.send_json({"error": "Unauthorized"})
        await websocket.close(code=4001)
        return

    log.info(f"WS CONNECTED: session_id={session_id}")
    db = SessionLocal()
    try:
        # ── RETURNING SESSION: answer submission ──────────────────────────────
        state = await _get_state(session_id)
        if state is not None:
            log.info(f"WS RESUME: session_id={session_id}, topics_covered={len(state.get('topics_covered', []))}")

            try:
                raw = await websocket.receive_text()
                payload = json.loads(raw)
                answer = payload.get("answer", "")
                action = payload.get("action", "")
                log.info(f"WS RECEIVED answer ({len(answer)} chars): {answer[:120]}...")
            except WebSocketDisconnect:
                log.info("WS DISCONNECTED before sending answer")
                return

            # ── Resume after page refresh ─────────────────────────────────────
            if action == "resume":
                log.info(f"WS RESUME ACTION: session_id={session_id}")
                if state.get("current_question"):
                    await websocket.send_json({
                        "type": "question",
                        "node": "resume",
                        "question": state["current_question"],
                        "topic": state.get("current_topic", ""),
                    })
                    log.info("WS SEND resumed question")
                else:
                    # Still in greeting phase — re-send greeting
                    greeting_text = next(
                        (a.get("content", "") for a in state.get("answers", [])
                         if a.get("role") == "interviewer" and a.get("topic") == "greeting"),
                        "Welcome back! Please respond whenever you're ready."
                    )
                    await websocket.send_json({"type": "greeting", "greeting": greeting_text})
                    log.info("WS SEND resumed greeting")
                return

            # ── Hint requested (practice mode only) ──────────────────────────
            if action == "hint":
                log.info(f"WS HINT: session_id={session_id}, practice={state.get('practice_mode')}")
                if state.get("practice_mode") and state.get("current_question"):
                    from app.agents.hint_agent import generate_hint_node
                    state["current_answer"] = answer  # partial answer so far
                    result = await _run_node_async("generate_hint", generate_hint_node, state)
                    await websocket.send_json({"type": "hint", "hint": result.get("hint_text", "")})
                else:
                    await websocket.send_json({"type": "hint", "hint": "Hints are only available in Practice mode."})
                return

            # ── Early end requested by user ───────────────────────────────────
            if action == "end":
                log.info(f"WS EARLY END: session_id={session_id}, user requested interview end")
                await _run_node_async("generate_report", report_agent_node, state)
                if not state.get("test_mode"):
                    try:
                        await _run_node_async("save_long_term_memory", save_memory_node, state)
                    except Exception as e:
                        log.error(f"Memory save failed (non-fatal): {e}")
                _persist_completed_session(session_id, state, db)
                scored_qas = [a for a in state.get("answers", []) if "question" in a and "scores" in a]
                await websocket.send_json({
                    "type": "report",
                    "report": state.get("final_report", {}),
                    "scored_qas": scored_qas,
                })
                await websocket.send_json({"type": "complete"})
                log.info("WS SEND early-end report + complete")
                await _del_state(session_id)
                return

            # Greeting response → generate first question
            if state.get("current_question") is None:
                log.info("WS GREETING RESPONSE received, generating first question")
                await _run_node_async("rag_retriever", rag_retriever_node, state)
                await _run_node_async("generate_question", generate_question_node, state)
                await websocket.send_json({
                    "type": "question",
                    "node": "generate_question",
                    "question": state["current_question"],
                    "topic": state.get("current_topic", ""),
                })
                log.info(f"WS SEND first question: topic='{state.get('current_topic', '')}'")
                await _set_state(session_id, state)
                return

            state["current_answer"] = answer

            # Evaluate the answer
            await _run_node_async("evaluate_answer", evaluator_node, state)

            # ── Trial limit check: stop at 3 scored answers ───────────────────
            scored_count = len([a for a in state.get("answers", []) if a.get("scores")])
            if state.get("session_type") == "trial" and scored_count >= 3:
                log.info(f"WS TRIAL LIMIT reached ({scored_count} scored answers)")
                await websocket.send_json({
                    "type": "trial_limit",
                    "message": (
                        "You've completed your free trial (3 questions). "
                        "Purchase interview credits to unlock a full 15–20 question interview."
                    ),
                    "scored_qas": [a for a in state.get("answers", []) if a.get("scores")],
                })
                await websocket.send_json({"type": "complete"})
                await _del_state(session_id)
                return

            # Check follow-up
            route = route_after_answer(state)
            log.info(f"ROUTE [after_answer]: -> {route}")
            if route == "follow_up_probe":
                await _run_node_async("follow_up_probe", followup_probe_node, state)
                await websocket.send_json({
                    "type": "question",
                    "node": "follow_up_probe",
                    "question": state["current_question"],
                    "topic": state.get("current_topic", ""),
                })
                await _set_state(session_id, state)
                log.info("WS SEND follow-up question")
                return

            # Check end or continue
            end_route = route_after_evaluation(state)
            log.info(f"ROUTE [after_evaluation]: -> {end_route}")
            if end_route == "end_interview":
                await _run_node_async("generate_report", report_agent_node, state)
                if not state.get("test_mode"):
                    try:
                        await _run_node_async("save_long_term_memory", save_memory_node, state)
                    except Exception as e:
                        log.error(f"Memory save failed (non-fatal): {e}")
                else:
                    log.info("TEST MODE: skipping long-term memory save")

                _persist_completed_session(session_id, state, db)
                scored_qas = [a for a in state.get("answers", []) if "question" in a and "scores" in a]
                await websocket.send_json({
                    "type": "report",
                    "report": state.get("final_report", {}),
                    "scored_qas": scored_qas,
                })
                await websocket.send_json({"type": "complete"})
                log.info("WS SEND report + complete, interview finished")
                await _del_state(session_id)
                return

            # Next question
            await _run_node_async("rag_retriever", rag_retriever_node, state)
            await _run_node_async("generate_question", generate_question_node, state)
            await websocket.send_json({
                "type": "question",
                "node": "generate_question",
                "question": state["current_question"],
                "topic": state.get("current_topic", ""),
            })
            log.info(f"WS SEND next question: topic='{state.get('current_topic', '')}'")
            await _set_state(session_id, state)
            return

        # ── FIRST CONNECTION: initialise ──────────────────────────────────────
        session = db.execute(
            select(InterviewSession).where(InterviewSession.session_id == uuid.UUID(session_id))
        ).scalar_one_or_none()

        if not session:
            log.warning(f"Session not found: {session_id}")
            await websocket.send_json({"error": "Session not found"})
            return

        if str(session.user_id) != requesting_user_id:
            log.warning(
                f"WS REJECTED (forbidden): session_id={session_id}, "
                f"owner={session.user_id}, requester={requesting_user_id}"
            )
            await websocket.send_json({"error": "Forbidden"})
            await websocket.close(code=4003)
            return

        jd_doc = db.execute(
            select(Document).where(
                Document.user_id == session.user_id,
                Document.doc_type == DocType.jd,
                Document.is_active == True,  # noqa: E712
            )
        ).scalar_one_or_none()

        resume_doc = db.execute(
            select(Document).where(
                Document.user_id == session.user_id,
                Document.doc_type == DocType.resume,
                Document.is_active == True,  # noqa: E712
            )
        ).scalar_one_or_none()

        if not jd_doc or not resume_doc:
            log.warning(f"Missing documents: jd={bool(jd_doc)}, resume={bool(resume_doc)}")
            await websocket.send_json({"error": "Please upload both JD and Resume before starting."})
            return

        log.info(f"Starting FRESH interview: session_id={session_id}, mode={session.mode.value}, type={session.session_type}")

        # Load user's custom LLM settings if enabled
        user_row = db.execute(
            select(User).where(User.user_id == session.user_id)
        ).scalar_one_or_none()
        user_flags = (user_row.feature_flags or {}) if user_row else {}
        user_openrouter_key   = user_flags.get("custom_openrouter_api_key") if user_flags.get("can_use_custom_llm") else None
        user_openrouter_model = user_flags.get("custom_openrouter_model")   if user_flags.get("can_use_custom_llm") else None

        is_test_mode = session.mode.value == "testing"
        state = {
            "session_id":        session_id,
            "user_id":           str(session.user_id),
            "session_type":      session.session_type,   # "trial" | "full" | "testing"
            "jd_text":           decrypt_content(jd_doc.content_encrypted),
            "resume_text":       decrypt_content(resume_doc.content_encrypted),
            "topics_covered":    [],
            "questions_asked":   [],
            "answers":           [],
            "scores":            [],
            "current_phase":     "init",
            "practice_mode":     session.mode.value == "practice",
            "test_mode":         is_test_mode,
            "follow_up_count":   0,
            "long_term_context": None,
            "parsed_profile":    None,
            "current_question":  None,
            "current_answer":    None,
            "current_topic":     None,
            "rag_context":       None,
            "follow_up_needed":  False,
            "interview_complete": False,
            "final_report":      None,
            "error":             None,
            # Custom LLM overrides (None = use system defaults)
            "user_openrouter_api_key": user_openrouter_key,
            "user_openrouter_model":   user_openrouter_model,
        }

        parse_result, memory_result = await asyncio.gather(
            asyncio.to_thread(parser_agent_node, state),
            asyncio.to_thread(load_memory_node, state),
        )
        _merge_state(state, parse_result)
        _merge_state(state, memory_result)
        await _run_node_async("greet_candidate", greet_candidate_node, state)

        greeting_text = ""
        for a in state.get("answers", []):
            if a.get("role") == "interviewer" and a.get("topic") == "greeting":
                greeting_text = a.get("content", "").strip()
                break

        if not greeting_text:
            greeting_text = "Hello! I'm Kate, your interviewer today. Let's get started — please tell me a bit about yourself."
            log.warning("WS SEND fallback greeting (greet_candidate_node returned empty)")
        else:
            log.info(f"WS SEND greeting ({len(greeting_text)} chars)")

        await websocket.send_json({"type": "greeting", "greeting": greeting_text})

        await _set_state(session_id, state)
        log.info("WS GREETING SENT: waiting for client greeting response on next connection")

    except WebSocketDisconnect:
        log.info(f"WS DISCONNECTED: session_id={session_id}")
    except Exception as e:
        log.error(f"WS ERROR: session_id={session_id}, error={type(e).__name__}: {e}", exc_info=True)
        try:
            await websocket.send_json({"error": str(e)})
        except RuntimeError:
            pass
    finally:
        log.info(f"WS CLEANUP: session_id={session_id}")
        db.close()
        try:
            await websocket.close()
        except RuntimeError:
            pass


@router.post("/{session_id}/answer")
async def submit_answer(
    session_id: str,
    body: AnswerRequest,
    user_id: str = Depends(get_current_user),
):
    """HTTP fallback endpoint for non-WebSocket clients."""
    return {"session_id": session_id, "received": True, "answer_length": len(body.answer)}


class EndAndReportRequest(BaseModel):
    messages: list


@router.post("/{session_id}/end-and-report")
async def end_and_report(session_id: str, body: EndAndReportRequest):
    """Generate a report from accumulated Q&A when the client ends the interview early."""
    from datetime import datetime, timezone

    log.info(f"END-AND-REPORT: session_id={session_id}, messages_count={len(body.messages)}")

    db = SessionLocal()
    try:
        session = db.execute(
            select(InterviewSession).where(InterviewSession.session_id == uuid.UUID(session_id))
        ).scalar_one_or_none()
        if session:
            session.status = "completed"
            session.ended_at = datetime.now(timezone.utc)
            db.commit()

        state = await _get_state(session_id)
        if state:
            await _del_state(session_id)
            answers = state.get("answers", [])
            scores = state.get("scores", [])
            parsed_profile = state.get("parsed_profile", {})
            practice_mode = state.get("practice_mode", False)
            log.info(f"END-AND-REPORT: Using Redis state ({len(answers)} answers)")
        else:
            answers, scores, parsed_profile, practice_mode = [], [], {}, False
            current_question = current_topic = None
            for msg in body.messages:
                if msg.get("role") == "interviewer":
                    current_question = msg.get("content", "")
                    current_topic = msg.get("topic", "")
                elif msg.get("role") == "candidate" and current_question:
                    answers.append({
                        "question": current_question,
                        "answer": msg.get("content", ""),
                        "topic": current_topic or "",
                        "scores": {},
                    })
                    current_question = None
            log.info(f"END-AND-REPORT: Built from client messages ({len(answers)} Q&A pairs)")

        if not answers:
            return {"report": {"aggregate_score": 0, "summary": "No questions were answered before ending."}}

        report_state = {
            "answers": answers,
            "scores": scores,
            "parsed_profile": parsed_profile,
            "practice_mode": practice_mode,
        }
        result = report_agent_node(report_state)
        return {"report": result.get("final_report", {})}
    finally:
        db.close()
