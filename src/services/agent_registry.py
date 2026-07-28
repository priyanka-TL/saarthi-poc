from dataclasses import dataclass
from typing import Optional, List, Dict
import time
from sqlalchemy import text

from pydantic import TypeAdapter

from src.logger import get_logger
from src.domain.agent_spec import AgentSpec

_agent_spec_adapter = TypeAdapter(AgentSpec)

logger = get_logger("agent_registry")

@dataclass
class RegisteredAgent:
    id: str
    key: str
    name: str
    description: str
    agent_type: str
    is_default: bool
    checksum: str
    spec: AgentSpec

class AgentRegistry:
    def __init__(self, ttl_s: float = 60.0):
        self._ttl_s = ttl_s
        self._snapshot: Dict[str, RegisteredAgent] = {}
        self._legacy_names: Dict[str, str] = {}
        self._version: int = 0
        self._loaded_at: float = 0.0
        self._max_updated_at = None

    def reload(self, session) -> int:
        try:
            # Query enabled agents joined with their active configuration
            query = text("""
                SELECT a.id, a.key, a.name, a.description, a.agent_type, a.is_default, a.updated_at, c.config, c.checksum
                FROM agents a
                JOIN agent_configurations c ON a.id = c.agent_id
                WHERE a.status = 'enabled' AND c.is_active = TRUE
            """)
            result = session.execute(query).fetchall()

            new_snapshot = {}
            new_legacy = {}
            max_ts = None

            for row in result:
                spec = _agent_spec_adapter.validate_python(row.config)
                agent = RegisteredAgent(
                    id=str(row.id),
                    key=row.key,
                    name=row.name,
                    description=row.description,
                    agent_type=row.agent_type,
                    is_default=row.is_default,
                    checksum=row.checksum,
                    spec=spec
                )
                new_snapshot[agent.key] = agent
                new_legacy[agent.name] = agent.key
                
                if max_ts is None or row.updated_at > max_ts:
                    max_ts = row.updated_at
                    
            self._snapshot = new_snapshot
            self._legacy_names = new_legacy
            self._version += 1
            self._loaded_at = time.monotonic()
            self._max_updated_at = max_ts
            logger.info(f"AgentRegistry reloaded version {self._version} with {len(self._snapshot)} agents")
            
        except Exception as e:
            # Failure to reload must LOG and keep the cached snapshot
            logger.error(f"Failed to reload AgentRegistry: {e}. Keeping cached snapshot.")
            
        return self._version

    def maybe_reload(self, session):
        """Called from before_request. Cheap: one indexed MAX(), TTL-gated."""
        if time.monotonic() - self._loaded_at < self._ttl_s:
            return
            
        try:
            query = text("SELECT MAX(updated_at) FROM agents WHERE status = 'enabled'")
            db_max = session.execute(query).scalar()
            
            if db_max != self._max_updated_at:
                self.reload(session)
            else:
                self._loaded_at = time.monotonic()
        except Exception as e:
            logger.error(f"Failed to check for AgentRegistry updates: {e}")

    @property
    def version(self) -> int:
        return self._version

    def routable(self) -> List[RegisteredAgent]:
        return [a for a in self._snapshot.values() if getattr(a.spec.routing, 'router_selectable', True)]
        
    def get_by_key_exact(self, key: str) -> Optional[RegisteredAgent]:
        return self._snapshot.get(key)
        
    def get(self, key_or_legacy_name: str) -> Optional[RegisteredAgent]:
        if key_or_legacy_name in self._snapshot:
            return self._snapshot[key_or_legacy_name]
        
        # fallback to name mapping
        key = self._legacy_names.get(key_or_legacy_name)
        if key:
            return self._snapshot.get(key)
        return None
        
    def default(self) -> Optional[RegisteredAgent]:
        for agent in self._snapshot.values():
            if agent.is_default:
                return agent
        return None
