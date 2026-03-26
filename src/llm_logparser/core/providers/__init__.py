from importlib import import_module
from typing import Callable, Any, Dict, Iterable

def get_provider(name: str) -> Callable[[Dict[str, Any]], Iterable[Dict[str, Any]]]:
    """
    Retrieves and returns adapter/get_adapter from providers.<name>.adapter.
    Only dynamic import is supported here (no legacy compatibility).
    """
    key = (name or "openai").lower()
    mod = import_module(f"{__name__}.{key}.adapter")
    if hasattr(mod, "get_adapter"):
        return mod.get_adapter()
    if hasattr(mod, "adapter"):
        return mod.adapter
    raise ValueError(f"Provider '{name}' has no adapter or get_adapter()")