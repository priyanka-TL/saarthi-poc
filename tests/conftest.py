"""Root test configuration.

ORDER IS LOAD-BEARING IN THIS FILE. Read the note below before editing.

``app.py`` constructs all four agents plus the orchestrator at *module scope*
(``app.py:19-27``), and each constructor calls ``get_llm()``
(``src/agents/base.py:21``). ``src/agents/base.py:3`` binds that name at *its*
import time via ``from src.llm import get_llm``.

So the stub must be installed **before ``src.agents.*`` is first imported**, which
in practice means before ``app`` is imported anywhere. pytest imports conftest
before any test module, and ``app`` is imported lazily inside a fixture, so the
window is safe --- provided no test module imports ``app`` at module level.
"""

from __future__ import annotations

import os

# ---------------------------------------------------------------------------
# 1. Environment, before anything reads it.
#
#    Config's class attributes are evaluated at import time (src/config.py), and
#    app.py:12-16 calls Config.validate() -> sys.exit(1) when the key is absent.
#    load_dotenv() defaults to override=False, so values set here win over the
#    real .env sitting next to this repo --- which is what keeps the suite from
#    depending on (or spending) a real credential.
# ---------------------------------------------------------------------------
os.environ["OPENROUTER_API_KEY"] = "test-key-never-used"
os.environ["OPENROUTER_MODEL"] = "test/scripted-model"
os.environ["LLM_TIMEOUT"] = "1"
os.environ["LLM_MAX_RETRIES"] = "1"
os.environ["LOG_LEVEL"] = "CRITICAL"

# ---------------------------------------------------------------------------
# 2. Install the stub BEFORE any src.agents.* import.
#
#    Importing src.llm pulls in src.config and src.logger only --- not src.agents
#    --- so this does not prematurely bind the real factory anywhere.
# ---------------------------------------------------------------------------
import src.llm  # noqa: E402

from tests.fakes import FakeDDGS, ScriptedChatModel  # noqa: E402

SHARED_MODEL = ScriptedChatModel()


def _fake_get_llm(temperature: float = 0.0) -> ScriptedChatModel:
    return SHARED_MODEL


src.llm.get_llm = _fake_get_llm  # type: ignore[assignment]

# The SAARTHI_REGISTRY=config path resolves its LLM client via
# LlmFactory.get(spec) (src/llm/factory.py), a different call site than the
# free-function src.llm.get_llm patched above. It's only ever invoked lazily
# (inside HandlerFactory.build(), itself called per-request), so patching the
# class method here -- at import time, well before any request -- is safe and
# has none of the fragile import-ordering constraints get_llm's patch has.
from src.llm.factory import LlmFactory  # noqa: E402


def _fake_llm_factory_get(self, spec) -> ScriptedChatModel:
    return SHARED_MODEL


LlmFactory.get = _fake_llm_factory_get  # type: ignore[assignment]

import pytest  # noqa: E402


# ---------------------------------------------------------------------------
# 3. Fixtures.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def app_module():
    """Import ``app`` and prove the stub actually took effect.

    Returns the ``app`` MODULE (not the Flask instance) -- callers that need
    module-level globals (``orchestrator``, ``chat_history``, ``flow_stops``,
    ``flow_title``) read them off this fixture directly. Use the ``flask_app``
    fixture for the Flask application instance itself (e.g. to build a test
    client).

    The assertions below are the suite's most important safety net. Without
    them, a mis-ordered import produces a *green* run that silently makes live,
    billed API calls and yields non-deterministic results --- the worst possible
    outcome for a regression baseline. Fail loudly instead.
    """
    import app as app_mod

    wired = [
        ("orchestrator router", app_mod.orchestrator.llm),
        *[
            (f"agent {name!r}", agent.llm)
            for name, agent in app_mod.orchestrator.agents.items()
        ],
    ]
    for label, llm in wired:
        assert llm is SHARED_MODEL, (
            f"{label} is holding a REAL LLM client, not the test stub. "
            f"tests/conftest.py patched src.llm.get_llm too late -- something "
            f"imported src.agents (or app) before the patch was applied. "
            f"Refusing to run: this suite would make live API calls."
        )

    assert len(app_mod.orchestrator.agents) == 4, (
        "Expected exactly 4 registered agents; the characterisation fixtures "
        "encode that count."
    )
    return app_mod


@pytest.fixture()
def flask_app(app_module):
    """The Flask application instance (as opposed to the ``app`` module)."""
    return app_module.app


@pytest.fixture()
def client(flask_app):
    """A test client for the Flask application."""
    return flask_app.test_client()


@pytest.fixture()
def script():
    """The shared scripted model, cleared before and after each test."""
    SHARED_MODEL.reset()
    yield SHARED_MODEL
    SHARED_MODEL.reset()


@pytest.fixture(autouse=True)
def reset_globals(request):
    """
    Process globals are shared by every request, so without this fixture test
    *order* changes test *results*.

    Deliberately calls the application's own ``_reset_flow()`` rather than
    reassigning the globals directly --- that keeps the fixture honest, and it
    exercises the reset path on every single test.
    """
    if (
        "app_module" in request.fixturenames
        or "flask_app" in request.fixturenames
        or "client" in request.fixturenames
    ):
        import app as app_mod

        app_mod._reset_flow()

        # HandlerFactory caches LlmAgentHandler instances per (key, checksum)
        # across requests -- correct production behaviour (tool bindings
        # shouldn't be rebuilt every turn), but it means bind_tools() is only
        # ever called once per agent for the life of the (session-scoped)
        # container, not once per test. Clear it so tests that inspect
        # script.bound_tools see a fresh call every time, same as the old
        # per-turn bind_tools() call in src/agents/base.py did.
        container = app_mod.app.config.get("CONTAINER")
        if container is not None:
            container.handler_factory._cache.clear()

        # In postgres mode, we must completely wipe the DB state to ensure test isolation
        # because the static user would otherwise pick up stale conversations from prior tests.
        from src.settings import settings
        if settings.saarthi_persistence == "postgres":
            from src.db.engine import SessionLocal
            from sqlalchemy import text
            with SessionLocal() as db_session:
                db_session.execute(text("DELETE FROM conversation_messages;"))
                db_session.execute(text("DELETE FROM conversations;"))
                db_session.commit()
                
            # Also call /api/reset to clear any memory state just in case, though
            # we mainly rely on the DB delete.
            client = request.getfixturevalue("client")
            client.post("/api/reset")
        
        yield
        app_mod._reset_flow()
    else:
        yield


@pytest.fixture()
def fake_ddgs(monkeypatch):
    """Replace the search provider inside ``src.tools.search_tools``.

    ``src/tools/search_tools.py:2`` does ``from ddgs import DDGS`` into ITS OWN
    module namespace, so the name to patch is ``src.tools.search_tools.DDGS``
    --- patching ``src.tools.DDGS`` (the package, not the submodule) or
    ``ddgs.DDGS`` would be too late/wrong target.
    """
    import src.tools.search_tools

    FakeDDGS.reset()
    monkeypatch.setattr(src.tools.search_tools, "DDGS", FakeDDGS)
    yield FakeDDGS
    FakeDDGS.reset()
