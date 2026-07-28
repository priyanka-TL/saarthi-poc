from typing import List
from src.logger import get_logger
from src.llm import get_llm
from .base import BaseAgent
from .specialized import GeneralSupportAgent
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser

logger = get_logger("orchestrator")

class OrchestratorAgent:
    """
    The Main Agent (Router) built with LangChain.
    It receives the initial request, uses a LangChain LCEL chain to classify the intent based 
    on the available sub-agents' descriptions, and delegates the task.
    """
    def __init__(self, agents: List[BaseAgent], default_agent: BaseAgent = None):
        self.name = "Main Router Agent"
        self.agents = {agent.name: agent for agent in agents}
        self.default_agent = default_agent or GeneralSupportAgent()
        self.llm = get_llm(temperature=0.0)
        
        # Build the system prompt dynamically with the agent details
        # This is a zero-shot classification prompt.
        system_prompt = (
            "You are a router for a customer support system. "
            "Given a user request, classify it into exactly one of the following agent categories based on their descriptions. "
            "Only reply with the exact 'Agent Name', nothing else.\n\n"
        )
        
        # We loop through all available agents and append their descriptions
        for name, agent in self.agents.items():
            system_prompt += f"- Agent Name: '{name}'\n  Description: {agent.description}\n"
            
        system_prompt += f"\nIf none match, reply with '{self.default_agent.name}'."
        
        # Build the LangChain classification pipeline
        # 1. 'prompt' templates the messages
        # 2. 'self.llm' calls the model
        # 3. 'StrOutputParser' extracts the raw text from the model's response
        prompt = ChatPromptTemplate.from_messages([
            ("system", system_prompt),
            ("human", "{request}")
        ])
        
        self.router_chain = prompt | self.llm | StrOutputParser()

    def _decide_sub_agent(self, request: str) -> BaseAgent:
        logger.info(f"[{self.name}] Received new user request: '{request}'")
        
        logger.debug(f"[{self.name}] Asking LLM to classify intent...")
        try:
            # Invoke the pipeline to get the classification string
            category = self.router_chain.invoke({"request": request})
            
            if category:
                logger.debug(f"[{self.name}] LLM Output: '{category}'")
                
                # Find the matching agent. 
                # We use 'in' and case-insensitive matching for robustness, 
                # because LLMs sometimes add punctuation or varying capitalization.
                for name, agent in self.agents.items():
                    if name.lower() in category.lower():
                        logger.info(f"[{self.name}] Decision: Routing to {name}")
                        return agent
            else:
                logger.warning(f"[{self.name}] LLM failed to return a valid category.")
                
        except Exception as e:
            logger.error(f"[{self.name}] Error during classification: {e}")
            
        logger.info(f"[{self.name}] Decision: Routing to {self.default_agent.name} (Fallback)")
        return self.default_agent

    def handle_request(self, request: str, history: list = None) -> dict:
        """
        The entry point for the user request.
        Returns a dictionary with the agent name and the response.
        """
        # Step 1: Decide which sub-agent is best suited
        selected_agent = self._decide_sub_agent(request)
        
        # Step 2: Delegate the request
        res_dict = selected_agent.process(request, history)
        
        # Step 3: Return the response with context
        return {
            "agent_name": selected_agent.name,
            "response": res_dict["content"],
            "sources": res_dict.get("sources", [])
        }
