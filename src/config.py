import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

class Config:
    # ------------------------------------------------------------
    # LLM Settings (LiteLLM -> OpenRouter)
    # ------------------------------------------------------------
    # All LLM calls go through LiteLLM, routed to OpenRouter.
    OPENROUTER_API_KEY: str = os.getenv("OPENROUTER_API_KEY", "")

    # Configurable model id (LiteLLM/OpenRouter naming, e.g. "qwen/qwen3.7-flash").
    OPENROUTER_MODEL: str = os.getenv("OPENROUTER_MODEL", "qwen/qwen3.7-flash")

    # Request timeout (seconds) and retry count applied to every LLM call.
    LLM_TIMEOUT: float = float(os.getenv("LLM_TIMEOUT", "30"))
    LLM_MAX_RETRIES: int = int(os.getenv("LLM_MAX_RETRIES", "3"))

    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO").upper()

    @classmethod
    def validate(cls):
        """
        Ensure critical configuration is set before the app starts.
        """
        if not cls.OPENROUTER_API_KEY:
            raise ValueError(
                "OPENROUTER_API_KEY is missing. "
                "Please set it in your .env file (see .env.example)."
            )

config = Config()
