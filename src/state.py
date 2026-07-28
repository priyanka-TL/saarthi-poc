from src.agents import HealthTipAgent, TechnicalAgent, GeneralSupportAgent, OrchestratorAgent, ResearchAgent

# Initialize the Multi-Agent System
health_tip_agent = HealthTipAgent()
technical_agent = TechnicalAgent()
general_agent = GeneralSupportAgent()
research_agent = ResearchAgent()

orchestrator = OrchestratorAgent(
    agents=[health_tip_agent, technical_agent, general_agent, research_agent],
    default_agent=general_agent
)

# In-memory session for chat history (cleared on restart)
chat_history = []

# Tracks the sequence of distinct agents that have handled this conversation,
# so the UI can render a breadcrumb/stepper of the real hand-off path.
flow_stops = []
flow_title = None

def _reset_flow():
    global flow_title
    chat_history.clear()
    flow_stops.clear()
    flow_title = None

def _record_flow_stop(agent_name, user_message):
    """Appends a new stop when the responding agent differs from the last one."""
    global flow_title
    if flow_title is None:
        flow_title = user_message if len(user_message) <= 60 else user_message[:57] + "..."
    if not flow_stops or flow_stops[-1] != agent_name:
        flow_stops.append(agent_name)

def _flow_payload():
    return {
        "title": flow_title,
        "stops": flow_stops,
        "current_index": len(flow_stops) - 1
    }
