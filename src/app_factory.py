from flask import Flask

def create_app():
    # Import settings to trigger validation on startup
    import src.settings
    from src import settings

    app = Flask(__name__, template_folder="../templates", static_folder="../static")

    from src.services.identity import build_provider
    app.config["USER_PROVIDER"] = build_provider(settings)

    # Register Blueprints
    from src.api.chat_routes import chat_bp
    from src.api.agent_routes import agent_bp

    app.register_blueprint(chat_bp)
    app.register_blueprint(agent_bp)

    # Register request hooks
    from src.api.deps import before_request, after_request
    app.before_request(before_request)
    app.after_request(after_request)

    return app
