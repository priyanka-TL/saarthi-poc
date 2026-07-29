"""Pins the settings that matter in src/config/agents/record_stories.yaml
(design doc §11.2). RemoteSpec and its nested models don't set
extra="forbid" (only BaseAgentSpec does -- verified in src/domain/agent_spec.py),
so a typo'd field name inside `remote:`/`routing:` would be silently ignored
rather than caught at config-sync time. These assertions are what catch that
instead.
"""
from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import TypeAdapter

from src.domain.agent_spec import AgentSpec, RemoteFlowAgentSpec

YAML_PATH = Path(__file__).parents[2] / "src" / "config" / "agents" / "record_stories.yaml"

_adapter = TypeAdapter(AgentSpec)


def _load_spec() -> RemoteFlowAgentSpec:
    raw = yaml.safe_load(YAML_PATH.read_text())
    spec = _adapter.validate_python(raw)
    assert isinstance(spec, RemoteFlowAgentSpec)
    return spec


def test_key_and_agent_type():
    spec = _load_spec()
    assert spec.key == "record_stories"
    assert spec.agent_type == "remote_flow"


def test_pin_session_is_true():
    """Without this the interview dies on turn 2 -- SessionService.open_for
    only creates a session row for a pin_session agent."""
    assert _load_spec().routing.pin_session is True


def test_exit_keywords_are_present():
    """Mandatory when pinning, or a user has no way out of the interview."""
    assert len(_load_spec().routing.exit_keywords) > 0


def test_priority_is_90_for_deterministic_pre_route():
    assert _load_spec().routing.priority == 90


def test_memory_strategy_is_none_not_recent():
    """Not an oversight -- Mitra reconstructs interview state server-side;
    sending history would corrupt it."""
    assert _load_spec().memory.strategy == "none"


def test_emit_options_feature_is_enabled():
    assert _load_spec().features.emit_options is True


def test_remote_env_var_names():
    remote = _load_spec().remote
    assert remote.bot_route_env == "MITRA_STORY_BOT_ROUTE"
    assert remote.company_env == "MITRA_COMPANY"
    assert remote.provider == "mitra"
    assert remote.flow_name == "guest-mi-story"


def test_router_and_direct_selectable():
    routing = _load_spec().routing
    assert routing.router_selectable is True
    assert routing.direct_selectable is True


def test_default_is_false_so_general_support_remains_the_sole_default():
    assert _load_spec().default is False
