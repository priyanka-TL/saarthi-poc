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

# Global singleton for tests
CURRENT_CONVERSATION_ID = None

def _reset_flow():
    """Stub to support tests/characterisation/test_flow_breadcrumb.py"""
    global CURRENT_CONVERSATION_ID
    CURRENT_CONVERSATION_ID = None
