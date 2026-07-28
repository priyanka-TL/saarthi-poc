import uuid
from typing import Optional, List, Dict, Any

from sqlalchemy.orm import Session

from src.repositories.conversations import ConversationRepository
from src.repositories.messages import MessageRepository
from src.domain.core import UserContext, MemorySpec
from src.domain.conversations import ConversationDTO
from src.services.agent_ids import agent_name_to_id, get_agent_name_from_id


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

    def allocate_and_persist_user_message(self, conversation_id: uuid.UUID, content: str) -> int:
        """
        Locks the conversation, gets the next seq, inserts the user message, and computes
        the exact truncation rules for the title (57 chars + ellipsis if > 60).
        """
        seq = self._conv_repo.next_seq_for_update(conversation_id)
        
        self._msg_repo.insert(
            conversation_id=conversation_id,
            seq=seq,
            role="user",
            content=content
        )
        
        title = content if len(content) <= 60 else content[:57] + "..."
        self._conv_repo.touch(conversation_id, title_from=title)
        
        return seq

    def persist_assistant_message(self, conversation_id: uuid.UUID, content: str, agent_name: str) -> int:
        """
        Persists the assistant's response, tracking the specific agent that generated it.
        """
        seq = self._conv_repo.next_seq_for_update(conversation_id)
        
        agent_id = agent_name_to_id(agent_name)
        
        self._msg_repo.insert(
            conversation_id=conversation_id,
            seq=seq,
            role="assistant",
            content=content,
            agent_id=agent_id
        )
        
        self._conv_repo.touch(conversation_id)
        return seq

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
        from sqlalchemy import text
        stmt = self._session.execute(
            text("SELECT title FROM conversations WHERE id = :id"), 
            {"id": conversation_id}
        )
        row = stmt.fetchone()
        title = row[0] if row else None
        
        agent_ids = self._msg_repo.distinct_agent_sequence(conversation_id)
        
        stops = []
        for aid in agent_ids:
            name = get_agent_name_from_id(aid)
            if name:
                stops.append(name)
                
        return {
            "title": title,
            "stops": stops,
            "current_index": len(stops) - 1 if stops else -1
        }

    def reset(self, conversation_id: uuid.UUID) -> None:
        """
        Archives the given conversation.
        """
        self._conv_repo.archive(conversation_id)
