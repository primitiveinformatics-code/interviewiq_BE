import uuid
from datetime import datetime
from sqlalchemy import Column, String, Text, Boolean, Integer, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from pgvector.sqlalchemy import Vector
from app.db.database import Base
import enum
from sqlalchemy import Enum

class SessionMode(str, enum.Enum):
    practice = "practice"
    assessment = "assessment"
    testing = "testing"

class DocType(str, enum.Enum):
    jd = "jd"
    resume = "resume"

class User(Base):
    __tablename__ = "users"
    user_id            = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email              = Column(String, unique=True, nullable=False, index=True)
    oauth_provider     = Column(String, nullable=False)   # "google" | "github" | "local"
    password_hash      = Column(String, nullable=True)    # only set for local (email/password) accounts
    created_at         = Column(DateTime, default=datetime.utcnow)
    encrypted_profile  = Column(Text)
    # ── Billing ───────────────────────────────────────────────────────────
    interview_credits  = Column(Integer, nullable=False, default=0)
    trial_used         = Column(Boolean, nullable=False, default=False)
    stripe_customer_id = Column(String, nullable=True, unique=True)
    # feature_flags: per-user admin overrides, e.g. {"extra_credits": 2, "test_mode": true}
    feature_flags      = Column(JSONB, nullable=True, default=dict)
    # ── Relationships ─────────────────────────────────────────────────────
    sessions           = relationship("Session", back_populates="user")
    documents          = relationship("Document", back_populates="user")
    long_term_memories = relationship("LongTermMemory", back_populates="user")

class Session(Base):
    __tablename__ = "sessions"
    session_id   = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id      = Column(UUID(as_uuid=True), ForeignKey("users.user_id"), nullable=False)
    started_at   = Column(DateTime, default=datetime.utcnow)
    ended_at     = Column(DateTime)
    mode         = Column(Enum(SessionMode), nullable=False)
    # session_type: "trial" (3-question free) | "full" (paid, 15-20 questions) | "testing" (admin)
    session_type = Column(String, nullable=False, default="full")
    status       = Column(String, default="active")
    pod_id       = Column(String)
    user         = relationship("User", back_populates="sessions")
    qa_pairs     = relationship("InterviewQA", back_populates="session")

class Document(Base):
    __tablename__ = "documents"
    doc_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.user_id"), nullable=False)
    doc_type = Column(Enum(DocType), nullable=False)
    version = Column(Integer, default=1)
    content_encrypted = Column(Text, nullable=False)
    uploaded_at = Column(DateTime, default=datetime.utcnow)
    is_active = Column(Boolean, default=True)
    user = relationship("User", back_populates="documents")

class InterviewQA(Base):
    __tablename__ = "interview_qa"
    qa_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id = Column(UUID(as_uuid=True), ForeignKey("sessions.session_id"), nullable=False)
    question = Column(Text, nullable=False)
    answer = Column(Text)
    topic = Column(String)
    follow_up_count = Column(Integer, default=0)
    scores = Column(JSONB)
    timestamp = Column(DateTime, default=datetime.utcnow)
    session = relationship("Session", back_populates="qa_pairs")

class LongTermMemory(Base):
    __tablename__ = "long_term_memory"
    memory_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.user_id"), nullable=False)
    session_id = Column(UUID(as_uuid=True), ForeignKey("sessions.session_id"))
    topic = Column(String)
    summary = Column(Text)
    embedding = Column(Vector(1024))
    created_at = Column(DateTime, default=datetime.utcnow)
    user = relationship("User", back_populates="long_term_memories")

class Coupon(Base):
    """Admin-generated coupon codes that grant interview credits on redemption."""
    __tablename__ = "coupons"
    coupon_id   = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    code        = Column(String, unique=True, nullable=False, index=True)   # e.g. "BETA2024"
    credits     = Column(Integer, nullable=False)                           # credits granted per redemption
    max_uses    = Column(Integer, nullable=True)                            # None = unlimited
    uses        = Column(Integer, nullable=False, default=0)                # times successfully redeemed
    is_active   = Column(Boolean, nullable=False, default=True)
    expires_at  = Column(DateTime, nullable=True)                           # None = never expires
    created_by  = Column(UUID(as_uuid=True), nullable=False)               # admin user_id
    created_at  = Column(DateTime, default=datetime.utcnow)
    note        = Column(String, nullable=True)                             # internal admin label


class CorpusChunk(Base):
    __tablename__ = "corpus_chunks"
    chunk_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    corpus_name = Column(String, nullable=False)
    domain = Column(String, nullable=False)
    content = Column(Text, nullable=False)
    embedding = Column(Vector(1024))
    source_url = Column(String)
    ingested_at = Column(DateTime, default=datetime.utcnow)
    ingested_by = Column(UUID(as_uuid=True))
