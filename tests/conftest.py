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
os.environ["LLM_MAX_RETRIES"] = "0"
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

import pytest  # noqa: E402


# ---------------------------------------------------------------------------
# 3. Fixtures.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def app_module():
    """Import ``app`` and prove the stub actually took effect.

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
    return app_mod.app


@pytest.fixture()
def client(app_module):
    """A test client for the Flask application."""
    return app_module.test_client()


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
    if "app_module" in request.fixturenames or "client" in request.fixturenames:
        import app as app_mod

        app_mod._reset_flow()
        yield
        app_mod._reset_flow()
    else:
        yield


@pytest.fixture()
def fake_ddgs(monkeypatch):
    """Replace the search provider inside ``src.tools``.

    ``src/tools.py:2`` does ``from ddgs import DDGS``, so the name to patch is
    ``src.tools.DDGS`` --- patching ``ddgs.DDGS`` would be too late.
    """
    import src.tools

    FakeDDGS.reset()
    monkeypatch.setattr(src.tools, "DDGS", FakeDDGS)
    yield FakeDDGS
    FakeDDGS.reset()
