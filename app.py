from src.app_factory import create_app

from src.state import orchestrator, _reset_flow

app = create_app()

if __name__ == "__main__":
    app.run(debug=True, port=5000)
