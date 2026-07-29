import uuid
from typing import Optional, List, Dict, Any

from sqlalchemy.orm import Session
from sqlalchemy import text

from src.repositories.conversations import ConversationRepository
from src.repositories.messages import MessageRepository
from src.domain.core import UserContext, MemorySpec
from src.domain.conversations import ConversationDTO, ConversationPageDTO


class ConversationService:
    def __init__(self, session: Session):
        self._session = session
        self._conv_repo = ConversationRepository(session)
        self._msg_repo = MessageRepository(session)

    def resolve(self, conversation_id: Optional[uuid.UUID], user: UserContext) -> ConversationDTO:
        """
        Gets or creates a conversation.
        """
        return self._conv_repo.get_or_create(conversation_id, user)

    def history(self, conversation_id: uuid.UUID, memory_spec: MemorySpec) -> List[Dict[str, str]]:
        """
        Returns recent messages bounded by the memory spec in chronological order.
        """
        msgs = self._msg_repo.recent(conversation_id, memory_spec)
        return [{"role": m.role, "content": m.content} for m in msgs]

    def flow_payload(self, conversation_id: uuid.UUID) -> Dict[str, Any]:
        """
        Reproduces the legacy module-global flow_payload exactly.
        """
        stmt = self._session.execute(
            text("SELECT title FROM conversations WHERE id = :id"), 
            {"id": conversation_id}
        )
        row = stmt.fetchone()
        title = row[0] if row else None
        
        agent_ids = self._msg_repo.distinct_agent_sequence(conversation_id)
        
        stops = []
        if agent_ids:
            # Fetch names in one query
            placeholders = ", ".join(f":id{i}" for i in range(len(agent_ids)))
            params = {f"id{i}": aid for i, aid in enumerate(agent_ids)}
            
            q = text(f"SELECT id, name FROM agents WHERE id IN ({placeholders})")
            agent_names = {row.id: row.name for row in self._session.execute(q, params).fetchall()}
            
            for aid in agent_ids:
                if aid in agent_names:
                    stops.append(agent_names[aid])
                
        return {
            "title": title,
            "stops": stops,
            "current_index": len(stops) - 1 if stops else -1
        }

    def list_recent(self, user: UserContext, limit: int) -> ConversationPageDTO:
        """
        Returns the user's most recently active conversations, newest first.
        """
        return self._conv_repo.list_for_user(user, cursor=None, limit=limit)

    def reset(self, conversation_id: uuid.UUID) -> None:
        """
        Archives the given conversation.
        """
        self._conv_repo.archive(conversation_id)
