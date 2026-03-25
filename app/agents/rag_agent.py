from app.agents.state import InterviewState
from app.core.embeddings import embed
from app.core.logging_config import get_logger
from app.db.database import SessionLocal
from sqlalchemy import text

log = get_logger("agent.rag")


def rag_retriever_node(state: InterviewState) -> dict:
    """Hybrid semantic + keyword retrieval from domain corpus using pgvector + tsvector."""
    if state.get("test_mode"):
        log.info("TEST MODE: skipping RAG retrieval")
        return {"rag_context": ""}

    topic = state.get("current_topic", "general")
    domain = (state.get("parsed_profile") or {}).get("domain", "SDE")
    query = f"{topic} {domain} technical interview question"
    log.info(f"RAG retrieval: topic={topic}, domain={domain}")

    try:
        log.debug(f"RAG embedding query: '{query}'")
        query_embedding = embed(query, input_type="search_query")
        log.debug(f"RAG embedding generated ({len(query_embedding)} dimensions)")

        db = SessionLocal()
        try:
            rows = db.execute(
                text("""
                    SELECT content,
                           (1 - (embedding <=> CAST(:emb AS vector))) * 0.7
                           + ts_rank(to_tsvector('english', content),
                                     plainto_tsquery('english', :q)) * 0.3 AS score
                    FROM corpus_chunks
                    WHERE domain = :domain
                       OR to_tsvector('english', content) @@ plainto_tsquery('english', :q)
                    ORDER BY score DESC
                    LIMIT 5
                """),
                {"emb": str(query_embedding), "q": query, "domain": domain}
            ).fetchall()
            rag_context = "\n---\n".join([r[0] for r in rows])
            log.info(f"RAG retrieved {len(rows)} chunks ({len(rag_context)} chars).")
            for i, r in enumerate(rows):
                log.debug(f"  RAG chunk {i+1} (score={r[1]:.3f}): {r[0][:120]}...")
        finally:
            db.close()
    except Exception as e:
        log.warning(f"RAG retrieval error (non-fatal): {e}")
        rag_context = ""

    return {"rag_context": rag_context}
