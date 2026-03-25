from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
import uuid, io, asyncio
from app.db.database import get_db
from app.db.models import Session as InterviewSession, InterviewQA
from app.core.security import get_current_user

router = APIRouter()


class IdealAnswerRequest(BaseModel):
    question: str
    topic: str = "general"
    experience_level: str = "mid"


@router.post("/ideal-answer")
async def get_ideal_answer(
    body: IdealAnswerRequest,
    user_id: str = Depends(get_current_user),
):
    """Generate a model answer for a given interview question."""
    from app.agents.ideal_answer_agent import generate_ideal_answer
    try:
        answer = await asyncio.to_thread(
            generate_ideal_answer,
            question=body.question,
            topic=body.topic,
            experience_level=body.experience_level,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to generate ideal answer: {e}")
    return {"ideal_answer": answer}

@router.get("/{session_id}")
async def get_report(
    session_id: str,
    user_id: str = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    try:
        session_uuid = uuid.UUID(session_id)
    except ValueError:
        raise HTTPException(status_code=422, detail=f"Invalid session_id: '{session_id}' is not a valid UUID")

    result = await db.execute(
        select(InterviewQA).where(InterviewQA.session_id == session_uuid)
    )
    qas = result.scalars().all()
    if not qas:
        raise HTTPException(status_code=404, detail="No interview data found for this session")

    per_question = [
        {"question": qa.question, "answer": qa.answer, "topic": qa.topic, "scores": qa.scores or {}}
        for qa in qas
    ]
    total = sum(q["scores"].get("overall_weighted", 0) for q in per_question)
    aggregate = round(total / max(len(qas), 1))
    return {
        "session_id":           session_id,
        "aggregate_score":      aggregate,
        "question_count":       len(qas),
        "per_question_breakdown": per_question,
    }

@router.get("/{session_id}/pdf")
async def download_pdf_report(
    session_id: str,
    user_id: str = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    try:
        session_uuid = uuid.UUID(session_id)
    except ValueError:
        raise HTTPException(status_code=422, detail=f"Invalid session_id: '{session_id}' is not a valid UUID")

    result = await db.execute(
        select(InterviewQA).where(InterviewQA.session_id == session_uuid)
    )
    qas = result.scalars().all()

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)

    c.setFont("Helvetica-Bold", 18)
    c.drawString(72, 760, "InterviewIQ — Session Report")
    c.setFont("Helvetica", 11)
    c.drawString(72, 740, f"Session ID: {session_id}")
    c.line(72, 732, 540, 732)

    y = 715
    for i, qa in enumerate(qas, 1):
        if y < 140:
            c.showPage()
            y = 760
        c.setFont("Helvetica-Bold", 11)
        c.drawString(72, y, f"Q{i} [{qa.topic or 'general'}]: {(qa.question or '')[:75]}...")
        y -= 18
        c.setFont("Helvetica", 10)
        answer_text = (qa.answer or "No answer provided")[:110]
        c.drawString(90, y, f"A: {answer_text}")
        y -= 16
        scores = qa.scores or {}
        score_line = (f"Score: {scores.get('overall_weighted', 'N/A')}/100  |  "
                      f"Tech: {scores.get('technical_accuracy', 'N/A')}  "
                      f"Depth: {scores.get('depth', 'N/A')}  "
                      f"PS: {scores.get('problem_solving', 'N/A')}")
        c.drawString(90, y, score_line)
        y -= 14
        if scores.get("feedback"):
            c.setFont("Helvetica-Oblique", 9)
            c.drawString(90, y, f"Feedback: {scores['feedback'][:90]}")
            y -= 22
        else:
            y -= 8

    c.save()
    buf.seek(0)
    return StreamingResponse(
        buf, media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename=interviewiq_report_{session_id[:8]}.pdf"}
    )

@router.get("/history/{user_id_param}")
async def session_history(
    user_id_param: str,
    user_id: str = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    if user_id != user_id_param:
        raise HTTPException(status_code=403, detail="Forbidden")
    result = await db.execute(
        select(InterviewSession)
        .where(InterviewSession.user_id == uuid.UUID(user_id))
        .order_by(InterviewSession.started_at.desc())
    )
    return [
        {
            "session_id":   str(s.session_id),
            "mode":         s.mode,
            "session_type": s.session_type,
            "status":       s.status,
            "started_at":   s.started_at,
            "ended_at":     s.ended_at,
        }
        for s in result.scalars().all()
    ]
