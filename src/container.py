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
    mitra_rest: Optional[Any] = None       # MitraRestClient | None (mitra_enabled gated)
    mitra_sessions: Optional[Any] = None   # MitraSessionManager | None (mitra_enabled gated)


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

    # mitra_rest / mitra_sessions: built when mitra_enabled is set. When
    # disabled (the default) both are None and LlmAgentHandler is unaffected
    # -- it never touches these fields. RemoteFlowAgentHandler checks for
    # None and raises a clear error if an operator enables a remote_flow
    # agent without setting MITRA_BASE_URL etc. The SAME instances are
    # threaded into both HandlerDeps (so RemoteFlowAgentHandler uses them)
    # and exposed directly on Container (so OrchestrationService's
    # finalisation logic shares the identical channel pool/REST client,
    # not a second, independent set).
    mitra_rest = None
    mitra_sessions = None
    if settings.mitra_enabled:
        from src.integrations.mitra.rest_client import from_settings as build_mitra_rest
        from src.integrations.mitra.session_manager import MitraSessionManager
        mitra_rest = build_mitra_rest(settings)
        mitra_sessions = MitraSessionManager(settings)

    deps = HandlerDeps(
        llm_factory=llm_factory,
        tool_registry=tool_registry,
        mitra_rest=mitra_rest,
        mitra_sessions=mitra_sessions,
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
        mitra_rest=mitra_rest,
        mitra_sessions=mitra_sessions,
    )
