import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from src.db.models import Conversation, ConversationStatusEnum
from src.domain.core import UserContext
from src.domain.conversations import ConversationDTO, ConversationPageDTO


class ConversationRepository:
    def __init__(self, session: Session):
        self._session = session

    def get_or_create(self, conversation_id: Optional[uuid.UUID], user: UserContext) -> ConversationDTO:
        """
        Gets an existing conversation by ID scoped to the tenant and external user.
        If the conversation_id is None or the conversation is not found for this user/tenant,
        creates a new conversation and returns it.
        """
        if conversation_id:
            stmt = select(Conversation).where(
                Conversation.id == conversation_id,
                Conversation.tenant_code == user.tenant_code,
                Conversation.external_user_id == user.user_id,
            )
            conv = self._session.execute(stmt).scalar_one_or_none()
            if conv:
                return ConversationDTO.model_validate(conv)

        # Not found or no ID provided -> Create new
        new_conv = Conversation(
            id=conversation_id or uuid.uuid4(),
            tenant_code=user.tenant_code,
            organization_id=user.organization_id,
            external_user_id=user.user_id,
            locale=user.locale,
            status=ConversationStatusEnum.active,
            message_count=0,
            metadata_={},
        )
        self._session.add(new_conv)
        self._session.flush() # flush to get defaults like created_at populated by db
        self._session.refresh(new_conv)
        return ConversationDTO.model_validate(new_conv)

    def next_seq_for_update(self, conversation_id: uuid.UUID) -> int:
        """
        Locks the conversation row (FOR UPDATE) and increments the message_count.
        Returns the new sequence number (which is the incremented message_count).
        """
        stmt = (
            update(Conversation)
            .where(Conversation.id == conversation_id)
            .values(message_count=Conversation.message_count + 1)
            .returning(Conversation.message_count)
        )
        result = self._session.execute(stmt).scalar_one()
        return result

    def touch(self, id: uuid.UUID, title_from: Optional[str] = None) -> None:
        """
        Updates last_message_at and optionally sets the title if it's currently null.
        """
        values_to_update = {"last_message_at": datetime.utcnow()}
        
        if title_from:
            # We want to set title only if it is currently None.
            # We can do this safely in SQL using COALESCE or just by reading/writing,
            # but since we want no transactions opened here, a single UPDATE with a condition
            # or a CASE statement is best.
            stmt = (
                update(Conversation)
                .where(Conversation.id == id)
                .values(
                    last_message_at=values_to_update["last_message_at"],
                    title=select(Conversation.title).where(Conversation.id == id).scalar_subquery().op("COALESCE")(title_from[:50])
                    # Wait, COALESCE(title, :title_from) is easier:
                )
            )
            # Actually, simpler:
            from sqlalchemy import func
            stmt = (
                update(Conversation)
                .where(Conversation.id == id)
                .values(
                    last_message_at=values_to_update["last_message_at"],
                    title=func.coalesce(Conversation.title, title_from[:50])
                )
            )
        else:
            stmt = (
                update(Conversation)
                .where(Conversation.id == id)
                .values(**values_to_update)
            )

        self._session.execute(stmt)

    def archive(self, id: uuid.UUID) -> None:
        """
        Sets the status of the conversation to archived.
        """
        stmt = (
            update(Conversation)
            .where(Conversation.id == id)
            .values(status=ConversationStatusEnum.archived)
        )
        self._session.execute(stmt)

    def pin(self, id: uuid.UUID, agent_id: uuid.UUID) -> None:
        """
        Pins the conversation to a specific agent.
        """
        stmt = (
            update(Conversation)
            .where(Conversation.id == id)
            .values(pinned_agent_id=agent_id)
        )
        self._session.execute(stmt)

    def unpin(self, id: uuid.UUID) -> None:
        """
        Unpins the conversation.
        """
        stmt = (
            update(Conversation)
            .where(Conversation.id == id)
            .values(pinned_agent_id=None)
        )
        self._session.execute(stmt)

    def list_for_user(self, user: UserContext, cursor: Optional[datetime], limit: int) -> ConversationPageDTO:
        """
        Returns a page of active conversations for the user using keyset pagination.
        """
        stmt = select(Conversation).where(
            Conversation.tenant_code == user.tenant_code,
            Conversation.external_user_id == user.user_id,
            Conversation.status == ConversationStatusEnum.active
        ).order_by(Conversation.last_message_at.desc().nullslast())

        if cursor:
            stmt = stmt.where(Conversation.last_message_at < cursor)

        # Fetch limit + 1 to check if there is a next page
        stmt = stmt.limit(limit + 1)
        
        rows = self._session.execute(stmt).scalars().all()
        
        has_next = len(rows) > limit
        page_rows = rows[:limit]
        
        next_cursor = None
        if has_next and page_rows:
            next_cursor = page_rows[-1].last_message_at

        return ConversationPageDTO(
            conversations=[ConversationDTO.model_validate(row) for row in page_rows],
            next_cursor=next_cursor
        )
