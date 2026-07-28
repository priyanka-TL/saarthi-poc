from typing import Any, List, Optional

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.messages import BaseMessage
from langchain_core.outputs import ChatResult
from langchain_litellm import ChatLiteLLM

from src.config import config
from src.logger import get_logger

logger = get_logger("llm_client")


class _NormalizedChatLiteLLM(ChatLiteLLM):
    """
    ChatLiteLLM, with message content normalized to a plain string.

    Reasoning-capable models (e.g. Qwen3) return `AIMessage.content` as a
    list of content blocks (thinking/reasoning + text) instead of a plain
    string. The rest of the app (agents, JSON API, frontend markdown
    rendering) expects `.content` to be a string, exactly as it was with the
    previous provider. Normalizing here -- in the provider layer -- keeps
    that contract intact without touching any agent/application logic.
    """

    def _generate(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Optional[CallbackManagerForLLMRun] = None,
        stream: Optional[bool] = None,
        **kwargs: Any,
    ) -> ChatResult:
        result = super()._generate(messages, stop=stop, run_manager=run_manager, stream=stream, **kwargs)
        for generation in result.generations:
            if isinstance(generation.message.content, list):
                generation.message.content = generation.message.text
        return result


def get_llm(temperature: float = 0.0) -> ChatLiteLLM:
    """
    Returns a LangChain-compatible chat model backed by LiteLLM, routed to
    OpenRouter. LiteLLM is the single abstraction layer for all LLM calls in
    this project -- provider/model selection, retries, and timeouts are all
    driven by configuration here rather than scattered across call sites.
    """
    # LiteLLM routes requests based on the "<provider>/<model>" prefix.
    model = f"openrouter/{config.OPENROUTER_MODEL}"

    logger.info(
        f"Initializing LLM via LiteLLM -> OpenRouter | model='{model}' "
        f"temperature={temperature} timeout={config.LLM_TIMEOUT}s "
        f"max_retries={config.LLM_MAX_RETRIES}"
    )

    return _NormalizedChatLiteLLM(
        model=model,
        api_key=config.OPENROUTER_API_KEY,
        temperature=temperature,
        request_timeout=config.LLM_TIMEOUT,
        max_retries=config.LLM_MAX_RETRIES,
    )
