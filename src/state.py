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

# Legacy memory mode state (for SAARTHI_PERSISTENCE=memory)
chat_history = []
flow_stops = []
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

# Global singleton for tests
CURRENT_CONVERSATION_ID = None

def _reset_flow():
    """Reset both memory and test singleton."""
    global flow_title, CURRENT_CONVERSATION_ID
    chat_history.clear()
    flow_stops.clear()
    flow_title = None
    CURRENT_CONVERSATION_ID = None
