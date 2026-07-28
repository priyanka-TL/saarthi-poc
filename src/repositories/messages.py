import uuid
from typing import Optional, List, Any, Dict

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.models import ConversationMessage, MessageRoleEnum
from src.domain.core import MemorySpec
from src.domain.conversations import MessageDTO, MessagePageDTO


class MessageRepository:
    def __init__(self, session: Session):
        self._session = session

    def insert(
        self,
        conversation_id: uuid.UUID,
        seq: int,
        role: str,
        content: str,
        agent_id: Optional[uuid.UUID] = None,
        agent_config_id: Optional[uuid.UUID] = None,
        agent_session_id: Optional[uuid.UUID] = None,
        route_reason: Optional[str] = None,
        route_confidence: Optional[float] = None,
        options: Optional[List[Dict[str, Any]]] = None,
        selected_option_id: Optional[str] = None,
        model: Optional[str] = None,
        prompt_tokens: Optional[int] = None,
        completion_tokens: Optional[int] = None,
        latency_ms: Optional[int] = None,
        error: Optional[str] = None,
        request_id: Optional[str] = None,
    ) -> MessageDTO:
        """
        Inserts a new message into the conversation history.
        """
        # Convert string role to enum
        role_enum = MessageRoleEnum(role)

        new_msg = ConversationMessage(
            id=uuid.uuid4(),
            conversation_id=conversation_id,
            seq=seq,
            role=role_enum,
            content=content,
            agent_id=agent_id,
            agent_config_id=agent_config_id,
            agent_session_id=agent_session_id,
            route_reason=route_reason,
            route_confidence=route_confidence,
            options=options,
            selected_option_id=selected_option_id,
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            latency_ms=latency_ms,
            error=error,
            request_id=request_id,
        )
        self._session.add(new_msg)
        self._session.flush() # ensure defaults like created_at are generated
        self._session.refresh(new_msg)
        
        return MessageDTO.model_validate(new_msg)

    def recent(self, conversation_id: uuid.UUID, memory: MemorySpec) -> List[MessageDTO]:
        """
        Returns the last `N` messages up to `memory.max_messages`, ordered chronologically (seq ASC).
        """
        if memory.max_messages <= 0:
            return []

        # We need the LAST max_messages, but returned in ASCENDING order.
        # This requires an inner query to get the last N descending, then outer query or just sort in python.
        stmt = (
            select(ConversationMessage)
            .where(ConversationMessage.conversation_id == conversation_id)
            .order_by(ConversationMessage.seq.desc())
            .limit(memory.max_messages)
        )
        rows = self._session.execute(stmt).scalars().all()
        
        # Reverse to get chronological order (seq ASC)
        rows = list(reversed(rows))
        
        return [MessageDTO.model_validate(row) for row in rows]

    def list_page(self, conversation_id: uuid.UUID, after_seq: int, limit: int) -> MessagePageDTO:
        """
        Returns a page of messages for a conversation, using keyset pagination on `seq`.
        """
        stmt = (
            select(ConversationMessage)
            .where(
                ConversationMessage.conversation_id == conversation_id,
                ConversationMessage.seq > after_seq
            )
            .order_by(ConversationMessage.seq.asc())
            .limit(limit + 1)
        )
        rows = self._session.execute(stmt).scalars().all()
        
        has_next = len(rows) > limit
        page_rows = rows[:limit]
        
        next_seq = None
        if has_next and page_rows:
            next_seq = page_rows[-1].seq

        return MessagePageDTO(
            messages=[MessageDTO.model_validate(row) for row in page_rows],
            next_seq=next_seq
        )

    def distinct_agent_sequence(self, conversation_id: uuid.UUID) -> List[uuid.UUID]:
        """
        Returns an ordered list of distinct agent_ids that have participated in the conversation,
        in the chronological order they first appeared. 
        Used to reconstruct the flow breadcrumbs.
        """
        # We want the agent_ids ordered by the minimum seq where they appeared
        from sqlalchemy import func
        
        stmt = (
            select(ConversationMessage.agent_id)
            .where(
                ConversationMessage.conversation_id == conversation_id,
                ConversationMessage.agent_id.is_not(None)
            )
            .group_by(ConversationMessage.agent_id)
            .order_by(func.min(ConversationMessage.seq))
        )
        return list(self._session.execute(stmt).scalars().all())
