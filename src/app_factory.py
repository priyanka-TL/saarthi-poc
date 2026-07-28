from flask import Flask

def create_app():
    # Import settings to trigger validation on startup
    import src.settings

    app = Flask(__name__, template_folder="../templates", static_folder="../static")

    # Register Blueprints
    from src.api.chat_routes import chat_bp
    from src.api.agent_routes import agent_bp

    app.register_blueprint(chat_bp)
    app.register_blueprint(agent_bp)

    return app
