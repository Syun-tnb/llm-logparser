from typing import Callable, Dict, Any, Iterable
from . import openai_chatgpt

_REGISTRY: Dict[str, Callable[[Dict[str, Any]], Iterable[Dict[str, Any]]]] = {
    "openai": openai_chatgpt.iter_messages,
}

def get_provider(name: str):
    key = (name or "openai").lower()
    if key in _REGISTRY:
        return _REGISTRY[key]
    raise ValueError(f"Unknown provider: {name}. Available: {', '.join(_REGISTRY)}")
