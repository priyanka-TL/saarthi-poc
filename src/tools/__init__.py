import os
import pkgutil
import importlib

# Re-export registry and UnknownToolError
from src.tools.registry import registry, UnknownToolError

# Auto-import all other python modules in this directory to register their tools
_package_dir = os.path.dirname(__file__)
for _, _module_name, _ in pkgutil.iter_modules([_package_dir]):
    if _module_name not in ("registry", "__init__"):
        importlib.import_module(f"{__name__}.{_module_name}")

# Shim re-exports so existing code does not break
from src.tools.search_tools import web_search_tool, youtube_search_tool

__all__ = [
    "registry",
    "UnknownToolError",
    "web_search_tool",
    "youtube_search_tool",
]
