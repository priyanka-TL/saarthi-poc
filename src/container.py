from dataclasses import dataclass
from typing import Any, Optional

from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker

from src.settings import Settings
from src.tools.registry import ToolRegistry
from src.llm.factory import LlmFactory
from src.agents.factory import HandlerFactory, HandlerDeps
from src.services.agent_registry import AgentRegistry


@dataclass(frozen=True)
class Container:
    """The composition root. Plain constructor injection, no DI framework.

    Singletons (engine, tool registry, llm factory, handler factory, agent
    registry, user provider) are built once, here. Per-request services are
    built fresh around the request's own db session elsewhere (src/api/deps.py,
    src/api/chat_routes.py) -- this dataclass only holds the long-lived pieces.
    """

    settings: Settings
    engine: Optional[Engine]
    session_factory: sessionmaker
    tool_registry: ToolRegistry
    llm_factory: LlmFactory
    handler_factory: HandlerFactory
    agent_registry: AgentRegistry
    user_provider: Any  # StaticTokenUserProvider | RequestTokenUserProvider


def build_container(settings: Settings) -> Container:
    # Reuse the EXISTING module-level engine/session-factory singletons
    # (src/db/engine.py) rather than constructing a second engine here --
    # a fresh engine would open a second connection pool against the same
    # database that src/api/deps.py's SessionLocal() also uses.
    from src.db.engine import engine as shared_engine, SessionLocal as shared_session_factory

    # Tools self-register onto a MODULE-LEVEL singleton (src.tools.registry.registry)
    # via @registry.register(...) decorators, fired only when their defining
    # module is imported. `import src.tools` walks every submodule and triggers
    # that registration. This must happen here, explicitly, before
    # ConfigSyncService.sync() validates any YAML's `tools:` references --
    # otherwise every tool reference would look "unknown".
    import src.tools  # noqa: F401  (import for registration side effect only)
    from src.tools.registry import registry as tool_registry

    llm_factory = LlmFactory()

    # mitra_rest / mitra_sessions: no MitraRestClient/MitraSessionManager
    # implementation exists yet (src/integrations/ is an empty package) and
    # mitra_enabled defaults to 0. HandlerDeps requires all 5 fields;
    # tests/unit/test_llm_handler.py already proves None is a safe value here
    # -- LlmAgentHandler never touches these two fields, and no "remote_flow"
    # handler is registered yet to need them.
    deps = HandlerDeps(
        llm_factory=llm_factory,
        tool_registry=tool_registry,
        mitra_rest=None,
        mitra_sessions=None,
        settings=settings,
    )
    handler_factory = HandlerFactory(deps)
    agent_registry = AgentRegistry(ttl_s=settings.registry_ttl_s)

    from src.services.identity import build_provider
    user_provider = build_provider(settings)

    return Container(
        settings=settings,
        engine=shared_engine,
        session_factory=shared_session_factory,
        tool_registry=tool_registry,
        llm_factory=llm_factory,
        handler_factory=handler_factory,
        agent_registry=agent_registry,
        user_provider=user_provider,
    )
