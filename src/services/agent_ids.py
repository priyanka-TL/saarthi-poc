import uuid
from typing import Optional
from src.state import orchestrator

AGENT_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_DNS, "saarthi.agents")

def agent_name_to_id(name: str) -> uuid.UUID:
    """Deterministically maps an agent name to a UUID for storage."""
    return uuid.uuid5(AGENT_NAMESPACE, name)

def get_agent_name_from_id(agent_id: uuid.UUID) -> Optional[str]:
    """
    Reverse-maps an agent UUID back to its string name using the registered agents.
    If the agent_id does not match any registered agent, returns None.
    """
    for name in orchestrator.agents.keys():
        if agent_name_to_id(name) == agent_id:
            return name
    return None
