import os
import yaml
from pathlib import Path
from typing import Literal, Dict, List, Any
from sqlalchemy import text
from src.logger import get_logger
from src.domain.agent_spec import AgentSpec, canonical_json

logger = get_logger("config_sync")

class SyncReport:
    def __init__(self):
        self.created: List[str] = []
        self.updated: List[str] = []
        self.unchanged: List[str] = []
        self.drifted: List[str] = []
        self.orphaned: List[str] = []
        
    def as_dict(self) -> Dict[str, Any]:
        return {
            "created": self.created,
            "updated": self.updated,
            "unchanged": self.unchanged,
            "drifted": self.drifted,
            "orphaned": self.orphaned
        }

def assert_unique(items):
    seen = set()
    for item in items:
        if item in seen:
            raise ValueError(f"Duplicate item found: {item}")
        seen.add(item)

def exactly_one(items):
    items_list = list(items)
    count = len(items_list)
    if count != 1:
        raise ValueError(f"Expected exactly 1 item, got {count}")

def audit(action: str, agent_id: str, note: str = "", before: str = None, after: str = None):
    logger.info(f"AUDIT [{action}] agent_id={agent_id} note={note}")

class ConfigSyncService:
    def __init__(self, tool_registry=None):
        self.tool_registry = tool_registry

    def sync(self, session, yaml_dir: Path, mode: Literal["safe", "force"] = "safe") -> SyncReport:
        # Acquire advisory lock
        session.execute(text("SELECT pg_advisory_xact_lock(hashtext('saarthi:agent_sync'))"))
        
        specs = []
        if yaml_dir.exists():
            for path in sorted(yaml_dir.glob("*.yaml")):
                with open(path, "r") as f:
                    raw = yaml.safe_load(f)
                
                # agent_spec.py resolve_and_store does the env expansion internally,
                # extra=forbid is defined on the model, so typos will crash here.
                from pydantic import TypeAdapter
                spec = TypeAdapter(AgentSpec).validate_python(raw)
                specs.append((path, spec))
                
        # 1-3. Global Invariants checked BEFORE DB writes
        assert_unique(s.key for _, s in specs)
        assert_unique(s.name for _, s in specs)
        
        default_agents = [s for _, s in specs if getattr(s, "default", False)]
        if default_agents:
            exactly_one(default_agents)
            
        for _, s in specs:
            if s.agent_type == "llm":
                if self.tool_registry:
                    self.tool_registry.assert_all_known(s.tools)
            else:
                bot_route_env = s.remote.bot_route_env
                assert os.getenv(bot_route_env), f"Missing environment variable: {bot_route_env}"
                
        report = SyncReport()
        
        for path, spec in specs:
            canonical, checksum = canonical_json(spec)
            
            # Get agent
            agent_row = session.execute(
                text("SELECT id FROM agents WHERE key = :key"),
                {"key": spec.key}
            ).fetchone()
            
            # Case 1: Brand new
            if not agent_row:
                res = session.execute(
                    text("""
                        INSERT INTO agents (key, name, description, agent_type, status, is_default, sort_order)
                        VALUES (:key, :name, :description, :agent_type, :status, :is_default, :sort_order)
                        RETURNING id
                    """),
                    {
                        "key": spec.key, "name": spec.name, "description": spec.description,
                        "agent_type": spec.agent_type, "status": spec.status,
                        "is_default": getattr(spec, "default", False), "sort_order": spec.sort_order
                    }
                )
                agent_id = res.scalar()
                
                session.execute(
                    text("""
                        INSERT INTO agent_configurations (agent_id, version, source, checksum, config, is_active, activated_at)
                        VALUES (:agent_id, 1, 'yaml', :checksum, :config, true, now())
                    """),
                    {"agent_id": agent_id, "checksum": checksum, "config": canonical}
                )
                audit("config_sync", str(agent_id), note=f"created from {path.name}", after=canonical)
                report.created.append(spec.key)
                continue
                
            agent_id = agent_row[0]
            
            # Identity columns follow YAML
            # status and is_default are deliberately NOT touched (§3.3).
            session.execute(
                text("""
                    UPDATE agents 
                    SET name = :name, description = :description, agent_type = :agent_type, sort_order = :sort_order, updated_at = now()
                    WHERE id = :agent_id
                """),
                {
                    "name": spec.name, "description": spec.description, "agent_type": spec.agent_type,
                    "sort_order": spec.sort_order, "agent_id": agent_id
                }
            )
            
            latest_yaml = session.execute(
                text("""
                    SELECT checksum FROM agent_configurations 
                    WHERE agent_id = :agent_id AND source = 'yaml'
                    ORDER BY version DESC LIMIT 1
                """),
                {"agent_id": agent_id}
            ).fetchone()
            
            if latest_yaml and latest_yaml[0] == checksum:
                report.unchanged.append(spec.key)
                continue
                
            # Case 2: YAML changed
            active_cfg = session.execute(
                text("""
                    SELECT version, source, config FROM agent_configurations 
                    WHERE agent_id = :agent_id AND is_active = true
                """),
                {"agent_id": agent_id}
            ).fetchone()
            
            adopt = (mode == "force") or (not active_cfg) or (active_cfg[1] == "yaml")
            
            new_v_row = session.execute(
                text("SELECT COALESCE(MAX(version), 0) + 1 FROM agent_configurations WHERE agent_id = :agent_id"),
                {"agent_id": agent_id}
            ).scalar()
            
            session.execute(
                text("""
                    INSERT INTO agent_configurations (agent_id, version, source, checksum, config, is_active, activated_at)
                    VALUES (:agent_id, :version, 'yaml', :checksum, :config, :is_active, CASE WHEN :is_active THEN now() ELSE NULL END)
                """),
                {"agent_id": agent_id, "version": new_v_row, "checksum": checksum, "config": canonical, "is_active": adopt}
            )
            
            if adopt:
                session.execute(
                    text("UPDATE agent_configurations SET is_active = false WHERE agent_id = :agent_id AND version != :keep_v"),
                    {"agent_id": agent_id, "keep_v": new_v_row}
                )
                audit("config_activate", str(agent_id), before=active_cfg[2] if active_cfg else None, after=canonical)
                report.updated.append(spec.key)
            else:
                report.drifted.append(spec.key)
                audit("config_sync", str(agent_id), after=canonical, note=f"yaml v{new_v_row} stored INACTIVE; db override v{active_cfg[0]} retained")

        # Case 3: orphans (agents sourced from YAML originally but no longer in the yaml dir)
        yaml_keys = {s.key for _, s in specs}
        
        # We find agents whose ACTIVE configuration is YAML-sourced, but who are not in the current yaml_keys.
        orphans = session.execute(
            text("""
                SELECT a.id, a.key FROM agents a
                WHERE a.id IN (
                    SELECT agent_id FROM agent_configurations WHERE source = 'yaml' AND is_active = true
                )
            """)
        ).fetchall()
        
        for orphan_id, orphan_key in orphans:
            if orphan_key not in yaml_keys:
                # Disabling the agent. Never delete.
                session.execute(
                    text("UPDATE agents SET status = 'disabled', updated_at = now() WHERE id = :agent_id"),
                    {"agent_id": orphan_id}
                )
                audit("agent_disable", str(orphan_id), note="yaml file removed")
                report.orphaned.append(orphan_key)
                
        session.commit()
        logger.info(f"agent config sync: {report.as_dict()}")
        return report
