from src.app_factory import create_app

# Re-export state for tests (specifically tests/conftest.py which relies on these)
from src.state import orchestrator, chat_history, flow_stops, flow_title, _reset_flow

app = create_app()

if __name__ == "__main__":
    app.run(debug=True, port=5000)
