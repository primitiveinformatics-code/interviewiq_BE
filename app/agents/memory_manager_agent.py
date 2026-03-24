from app.agents.state import InterviewState
from app.core.embeddings import embed
from app.core.logging_config import get_logger
from app.db.database import SessionLocal
from app.db.models import LongTermMemory
from sqlalchemy import select
import uuid

log = get_logger("agent.memory")

def load_memory_node(state: InterviewState) -> dict:
    """Load prior session summaries from pgvector for this user."""
    user_id = state["user_id"]
    log.info(f"Loading long-term memory for user {user_id}")
    db = SessionLocal()
    try:
        memories = db.execute(
            select(LongTermMemory)
            .where(LongTermMemory.user_id == uuid.UUID(user_id))
            .order_by(LongTermMemory.created_at.desc())
            .limit(10)
        ).scalars().all()
        parts = [f"[{m.topic}]: {m.summary}" for m in memories]
        context = "\n".join(parts) if parts else "No prior sessions found."
        log.info(f"Loaded {len(memories)} memory entries.")
        for i, m in enumerate(memories):
            log.debug(f"  Memory {i+1}: topic='{m.topic}', summary='{m.summary[:100]}...'")
    except Exception as e:
        log.error(f"Memory load error: {e}")
        context = "No prior sessions found."
    finally:
        db.close()
    return {"long_term_context": context, "current_phase": "greeting"}


def save_memory_node(state: InterviewState) -> dict:
    """Embed and persist session Q&A summaries to pgvector after interview ends."""
    db = SessionLocal()
    qa_list = state.get("answers", [])
    log.info(f"Saving {len(qa_list)} memory entries for user {state['user_id']}")
    try:
        saved = 0
        for qa in qa_list:
            if not qa.get("question"):
                continue
            summary = f"Q: {qa.get('question', '')[:200]} | A: {qa.get('answer', '')[:200]}"
            topic = qa.get("topic", "general")
            emb = embed(summary)
            log.debug(f"  Saving memory: topic='{topic}', summary='{summary[:80]}...', embedding_dim={len(emb)}")
            db.add(LongTermMemory(
                user_id=uuid.UUID(state["user_id"]),
                session_id=uuid.UUID(state["session_id"]),
                topic=topic, summary=summary, embedding=emb
            ))
            saved += 1
        db.commit()
        log.info(f"Saved {saved} memory entries successfully.")
    except Exception as e:
        log.error(f"Memory save error: {e}")
        db.rollback()
    finally:
        db.close()
    return {"current_phase": "done"}
