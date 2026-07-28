"""Minimal, behaviour-preserving replacement for
OrchestratorAgent._decide_sub_agent (src/agents/orchestrator.py), retargeted at
AgentRegistry entries instead of BaseAgent instances, so SAARTHI_REGISTRY=config
makes the SAME routing decision as SAARTHI_REGISTRY=code today.

This is explicitly NOT "router v2 with five gates" (that is a later phase's
src/services/router_service.py, which does not exist yet). Delete this module
once that ships.
"""
from typing import List

from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser

from src.domain.agent_spec import ModelSpec
from src.services.agent_registry import RegisteredAgent
from src.logger import get_logger

logger = get_logger("config_mode_router")


def _router_model_spec() -> ModelSpec:
    # There is no YAML for "the router" -- it classifies, it doesn't answer.
    # Mirrors get_llm()'s own hardcoding (src/llm/__init__.py): same source
    # for the default model (config.OPENROUTER_MODEL / config.LLM_TIMEOUT, via
    # the legacy src.config.config shim) and the same temperature=0.0.
    from src.config import config
    return ModelSpec(
        provider="openrouter",
        name=config.OPENROUTER_MODEL,
        temperature=0.0,
        max_tokens=None,
        timeout_s=config.LLM_TIMEOUT,
    )


def decide_sub_agent(
    llm_factory,
    agents: List[RegisteredAgent],
    default_agent: RegisteredAgent,
    request: str,
) -> RegisteredAgent:
    """Mirrors OrchestratorAgent._decide_sub_agent exactly: same prompt
    structure, same case-insensitive substring match, same fallback -- just
    targeting RegisteredAgent (name/description) instead of BaseAgent."""
    llm = llm_factory.get(_router_model_spec())

    system_prompt = (
        "You are a router for a customer support system. "
        "Given a user request, classify it into exactly one of the following agent categories based on their descriptions. "
        "Only reply with the exact 'Agent Name', nothing else.\n\n"
    )
    for agent in agents:
        system_prompt += f"- Agent Name: '{agent.name}'\n  Description: {agent.description}\n"
    system_prompt += f"\nIf none match, reply with '{default_agent.name}'."

    prompt = ChatPromptTemplate.from_messages([
        ("system", system_prompt),
        ("human", "{request}"),
    ])
    chain = prompt | llm | StrOutputParser()

    try:
        category = chain.invoke({"request": request})
        if category:
            for agent in agents:
                if agent.name.lower() in category.lower():
                    return agent
    except Exception as e:
        logger.error(f"Error during classification: {e}")

    return default_agent
