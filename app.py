from src.app_factory import create_app, resolve_reloader
from src.settings import settings

from src.state import orchestrator, _reset_flow, chat_history, flow_stops, flow_title

app = create_app()

if __name__ == "__main__":
    app.run(debug=True, use_reloader=resolve_reloader(settings.mitra_enabled), port=5000)
