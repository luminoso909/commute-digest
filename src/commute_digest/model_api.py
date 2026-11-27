"""Model and credential selection scoped to one publishing or reading request."""

import os
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field


class EditorError(RuntimeError):
    """An API call or the returned editorial material was unusable."""


@dataclass(frozen=True)
class Provider:
    name: str
    base_url: str
    key_env: str


DEEPSEEK = Provider("DeepSeek", "https://api.deepseek.com", "DEEPSEEK_API_KEY")
DASHSCOPE = Provider("阿里云百炼", "https://dashscope.aliyuncs.com/compatible-mode/v1", "DASHSCOPE_API_KEY")
DEFAULT_MODEL = "deepseek-flash"
_MODELS = {
    DEFAULT_MODEL: ("DeepSeek Flash", DEEPSEEK),
    "deepseek-v4-pro": ("DeepSeek V4 Pro", DEEPSEEK),
    "qwen-plus": ("通义千问 Plus（百炼北京）", DASHSCOPE),
}
MODEL_OPTIONS = {model: label for model, (label, _) in _MODELS.items()}


@dataclass(frozen=True)
class Selection:
    model: str
    key: str = field(repr=False)
    deepseek_key: str = field(repr=False)


_selection: ContextVar[Selection | None] = ContextVar("digest_model", default=None)


def same_model_provider(first: str, second: str) -> bool:
    return first in _MODELS and second in _MODELS and _MODELS[first][1] == _MODELS[second][1]


@contextmanager
def use_model(model: str, api_key: str | None = None, *, deepseek_key: str | None = None) -> Iterator[None]:
    if model not in _MODELS:
        raise EditorError("请选择列表中的模型。")
    key = (api_key or "").strip()
    if not key:
        raise EditorError(f"请先填写{MODEL_OPTIONS[model]}的个人 API 密钥。")
    token = _selection.set(Selection(model, key, (deepseek_key or "").strip()))
    try:
        yield
    finally:
        _selection.reset(token)


def selected_model() -> str:
    selected = _selection.get()
    return selected.model if selected else DEFAULT_MODEL


def credentials(*, for_drafting: bool) -> tuple[Provider, str]:
    selected = _selection.get()
    if selected is None:
        # Only the shared publication worker and CLI run without an account context.
        provider = DEEPSEEK
        key = os.getenv(provider.key_env, "").strip()
        if not key:
            raise EditorError(f"共享日报缺少 {provider.key_env}。")
    else:
        selected_provider = _MODELS[selected.model][1]
        provider = selected_provider if for_drafting else DEEPSEEK
        key = selected.key if provider == selected_provider else selected.deepseek_key
        if not key:
            raise EditorError("联网采集和朗读需要个人 DeepSeek 密钥。请先在 API 设置中为一个 DeepSeek 模型保存密钥。")
    if not key.isascii() or not key.isprintable() or any(char.isspace() for char in key):
        raise EditorError("API 密钥应为一行连续的英文字符。")
    return provider, key
