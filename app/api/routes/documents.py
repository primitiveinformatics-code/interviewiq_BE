from fastapi import APIRouter, Depends, UploadFile, File, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update
import uuid
from app.db.database import get_db
from app.db.models import Document, DocType
from app.core.security import get_current_user
from app.core.crypto import encrypt_content, decrypt_content

router = APIRouter()

@router.post("/upload")
async def upload_document(
    doc_type: DocType,
    file: UploadFile = File(...),
    user_id: str = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    content = (await file.read()).decode("utf-8", errors="ignore")
    encrypted = encrypt_content(content)
    # Deactivate previous document of same type for this user
    await db.execute(
        update(Document)
        .where(Document.user_id == uuid.UUID(user_id), Document.doc_type == doc_type)
        .values(is_active=False)
    )
    doc = Document(
        user_id=uuid.UUID(user_id),
        doc_type=doc_type,
        content_encrypted=encrypted,
        is_active=True
    )
    db.add(doc)
    await db.flush()
    await db.refresh(doc)
    return {"doc_id": str(doc.doc_id), "doc_type": doc_type, "uploaded_at": doc.uploaded_at}

@router.put("/{doc_id}/replace")
async def replace_document(
    doc_id: str,
    doc_type: DocType,
    file: UploadFile = File(...),
    user_id: str = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    result = await db.execute(
        select(Document).where(
            Document.doc_id == uuid.UUID(doc_id),
            Document.user_id == uuid.UUID(user_id)
        )
    )
    doc = result.scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    content = (await file.read()).decode("utf-8", errors="ignore")
    doc.content_encrypted = encrypt_content(content)
    doc.version += 1
    return {"doc_id": doc_id, "version": doc.version, "message": "Document replaced successfully"}

@router.get("/{user_id_param}")
async def list_documents(
    user_id_param: str,
    user_id: str = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    if user_id != user_id_param:
        raise HTTPException(status_code=403, detail="Forbidden")
    result = await db.execute(
        select(Document).where(
            Document.user_id == uuid.UUID(user_id),
            Document.is_active == True
        )
    )
    return [
        {"doc_id": str(d.doc_id), "doc_type": d.doc_type, "version": d.version, "uploaded_at": d.uploaded_at}
        for d in result.scalars().all()
    ]
