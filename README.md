# NovaAssist - Production-Ready Multi-Agent Orchestration

This is a production-style, modular example of a multi-agent routing system in Python, built on top of **LangChain** and **Flask**. It demonstrates how a Main Agent (Orchestrator) receives a user query, dynamically queries an LLM to classify intent based on available sub-agents, and delegates the task using LangChain Expression Language (LCEL) pipelines.

## Key Production Features
1. **LangChain Powered**: Fully leverages LangChain's LCEL (`prompt | llm | parser`) for highly readable, composable, and standardized agent pipelines.
2. **No Hardcoding**: The routing logic does not have any hardcoded rules. The `OrchestratorAgent` dynamically constructs zero-shot classification prompts using the `name` and `description` of registered sub-agents.
3. **Direct Agent Communication**: The modern web interface allows you to select a specific agent from the sidebar and speak with them directly, bypassing the orchestrator altogether.
4. **Interactive UI with Theming**: Includes a polished, dynamic frontend with smooth micro-animations and a built-in Dark/Light mode toggle that saves to your local storage.
5. **Modular Architecture**: Code is split into specialized modules (`config`, `logger`, `llm`, `agents`) for easier maintenance and scalability.
6. **LiteLLM + OpenRouter**: All LLM calls go through [LiteLLM](https://www.litellm.ai/) as a single provider-agnostic abstraction layer, configured to route to [OpenRouter](https://openrouter.ai/). The model is fully configurable via environment variables, with built-in retries, request timeouts, and logging.

## Project Structure
- `src/config.py`: Loads environment variables (OpenRouter API key, model, timeout, retries).
- `src/logger.py`: Centralized logging configuration.
- `src/llm.py`: A LangChain factory that configures and returns `ChatLiteLLM` instances, routed through LiteLLM to OpenRouter.
- `src/agents/`:
  - `base.py`: The abstract base class that manages the core LangChain pipeline execution.
  - `specialized.py`: The implementations of specific sub-agents (Health, Technical, General).
  - `orchestrator.py`: The intelligent router logic built with a dedicated classification LCEL chain.
- `app.py`: The Flask web application entry point.
- `templates/` & `static/`: HTML, CSS (with theming), and JS for the web interface.

## Requirements
- Python 3.x
- `requests`
- `python-dotenv`
- `flask`
- `langchain`, `langchain-core`, `langchain-community`
- `litellm`, `langchain-litellm`

## Setup and Usage

1. **Set up a Virtual Environment**:
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   ```

2. **Install Dependencies**:
   Ensure your virtual environment is active, then run:
   ```bash
   pip install -r requirements.txt
   ```

3. **Configure Environment**:
   Create your `.env` file and add your [OpenRouter](https://openrouter.ai/keys) API key.
   ```bash
   cp .env.example .env
   # Edit .env and set OPENROUTER_API_KEY
   ```

   Environment variables:
   | Variable | Required | Default | Description |
   |---|---|---|---|
   | `OPENROUTER_API_KEY` | Yes | — | Your OpenRouter API key. The app fails fast at startup if this is missing. |
   | `OPENROUTER_MODEL` | No | `qwen/qwen3.7-flash` | Any model id available on [OpenRouter](https://openrouter.ai/models). Passed to LiteLLM as `openrouter/<model>`. |
   | `LLM_TIMEOUT` | No | `30` | Per-request timeout (seconds) applied by LiteLLM to every LLM call. |
   | `LLM_MAX_RETRIES` | No | `3` | Retry count applied by LiteLLM on transient LLM call failures. |
   | `LOG_LEVEL` | No | `INFO` | Logging verbosity (`DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`). |

4. **Run the application**:
   Ensure you are still inside the virtual environment (`source .venv/bin/activate`), and then run:
   ```bash
   flask run
   ```
   
   The web application will be available at `http://127.0.0.1:5000/`.
