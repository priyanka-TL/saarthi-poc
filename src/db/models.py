from datetime import datetime
import enum
import uuid
from typing import Optional, Dict, Any, List

from sqlalchemy import MetaData, String, Integer, DateTime, func, CheckConstraint, ForeignKey, UniqueConstraint, Index, Numeric
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID, JSONB
import sqlalchemy as sa

NAMING = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

class Base(DeclarativeBase):
    """
    Base class for SQLAlchemy declarative models.
    Provides standard naming conventions for constraints so Alembic autogenerate
    produces stable, diffable names.
    """
    metadata = MetaData(naming_convention=NAMING)

class ConversationStatusEnum(enum.Enum):
    active = "active"
    archived = "archived"

class MessageRoleEnum(enum.Enum):
    user = "user"
    assistant = "assistant"
    system = "system"
    tool = "tool"

class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()"))
    tenant_code: Mapped[str] = mapped_column(String, nullable=False)
    organization_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    external_user_id: Mapped[str] = mapped_column(String, nullable=False)
    title: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    
    # We specify create_type=False because Alembic doesn't handle ENUM creation cleanly.
    status: Mapped[ConversationStatusEnum] = mapped_column(
        sa.Enum(ConversationStatusEnum, name="conversation_status_enum", create_type=False),
        nullable=False,
        server_default='active'
    )
    
    pinned_agent_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True) # FK deferred
    locale: Mapped[str] = mapped_column(String, nullable=False, server_default='en')
    message_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default='0')
    metadata_: Mapped[Dict[str, Any]] = mapped_column("metadata", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb"))
    last_message_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        CheckConstraint("locale IN ('en', 'hi', 'kn', 'te')", name="conv_locale"),
        CheckConstraint("message_count >= 0", name="conv_msgcount"),
        CheckConstraint("tenant_code <> ''", name="conv_tenant"),
        Index("ix_conv_user_recent", "tenant_code", "external_user_id", sa.text("last_message_at DESC NULLS LAST"), postgresql_where=sa.text("status = 'active'")),
        Index("ix_conv_org", "tenant_code", "organization_id", sa.text("last_message_at DESC NULLS LAST"), postgresql_where=sa.text("status = 'active' AND organization_id IS NOT NULL")),
        Index("ix_conv_pinned", "pinned_agent_id", postgresql_where=sa.text("pinned_agent_id IS NOT NULL")),
    )

class ConversationMessage(Base):
    __tablename__ = "conversation_messages"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()"))
    conversation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    
    role: Mapped[MessageRoleEnum] = mapped_column(
        sa.Enum(MessageRoleEnum, name="message_role_enum", create_type=False),
        nullable=False
    )
    content: Mapped[str] = mapped_column(String, nullable=False)

    # attribution (FKs deferred for agent_*, only adding columns as requested)
    agent_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)
    agent_config_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)
    agent_session_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)
    route_reason: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    route_confidence: Mapped[Optional[float]] = mapped_column(Numeric(4, 3), nullable=True)

    # interaction affordances
    options: Mapped[Optional[List[Dict[str, Any]]]] = mapped_column(JSONB, nullable=True)
    selected_option_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    # telemetry
    model: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    prompt_tokens: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    latency_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    error: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    request_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("conversation_id", "seq", name="uq_msg_seq"),
        CheckConstraint("seq > 0", name="msg_seq"),
        CheckConstraint("role <> 'assistant' OR agent_id IS NOT NULL", name="msg_assistant_attribution"),
        CheckConstraint("options IS NULL OR role = 'assistant'", name="msg_options_only_assistant"),
        CheckConstraint("COALESCE(prompt_tokens, 0) >= 0 AND COALESCE(completion_tokens, 0) >= 0", name="msg_tokens"),
        CheckConstraint("COALESCE(latency_ms, 0) >= 0", name="msg_latency"),
        CheckConstraint("route_confidence IS NULL OR (route_confidence >= 0 AND route_confidence <= 1)", name="msg_confidence"),
        Index("ix_msg_conv_seq", "conversation_id", "seq"),
        Index("ix_msg_agent_time", "agent_id", sa.text("created_at DESC")),
        Index("ix_msg_request", "request_id", postgresql_where=sa.text("request_id IS NOT NULL")),
        Index("ix_msg_session", "agent_session_id", postgresql_where=sa.text("agent_session_id IS NOT NULL")),
    )

