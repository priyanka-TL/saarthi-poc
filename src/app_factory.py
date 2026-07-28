from flask import Flask


def resolve_reloader(mitra_enabled) -> bool:
    """MITRA_ENABLED=1 must never run under the Werkzeug reloader: the
    reloader forks/re-execs a child process that re-imports and re-runs all
    module-level setup, which would double-boot Mitra's persistent
    websocket/session state.

    Raises rather than silently overriding -- `assert` is stripped entirely
    under `python -O`, which would silently disable this guard in exactly the
    deployment mode where catching the regression matters most.
    """
    use_reloader = not bool(mitra_enabled)
    if mitra_enabled and use_reloader:
        raise RuntimeError("MITRA_ENABLED=1 requires the Flask/Werkzeug reloader to be off.")
    return use_reloader


def create_app():
    # Import settings to trigger validation on startup
    from src.settings import settings

    app = Flask(__name__, template_folder="../templates", static_folder="../static")

    from src.container import build_container
    container = build_container(settings)
    app.config["CONTAINER"] = container
    app.config["USER_PROVIDER"] = container.user_provider

    if settings.saarthi_registry == "config":
        from src.bootstrap import sync_and_reload
        sync_and_reload(container)

    # Register Blueprints
    from src.api.chat_routes import chat_bp
    from src.api.agent_routes import agent_bp

    app.register_blueprint(chat_bp)
    app.register_blueprint(agent_bp)

    # Register request hooks
    from src.api.deps import before_request, after_request, teardown_request
    app.before_request(before_request)
    app.after_request(after_request)
    app.teardown_request(teardown_request)

    return app
