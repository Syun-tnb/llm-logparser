from importlib import import_module
from typing import Callable, Any, Dict, Iterable

def get_provider(name: str) -> Callable[[Dict[str, Any]], Iterable[Dict[str, Any]]]:
    """
    providers.<name>.adapter から adapter/get_adapter を取得して返す。
    ここでは動的importのみ（レガシー互換なし）。
    """
    key = (name or "openai").lower()
    mod = import_module(f"{__name__}.{key}.adapter")
    if hasattr(mod, "get_adapter"):
        return mod.get_adapter()
    if hasattr(mod, "adapter"):
        return mod.adapter
    raise ValueError(f"Provider '{name}' has no adapter or get_adapter()")