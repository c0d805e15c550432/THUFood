"""Generate comments through OpenAI-compatible or Anthropic APIs."""

import requests
from openai import OpenAI


class AIRequestError(RuntimeError):
    """Safe UI-facing failure that never contains credentials or response bodies."""


def _anthropic_chat(prompt, model, api_key, base_url):
    response = requests.post(
        base_url.rstrip("/") + "/v1/messages",
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": model,
            "max_tokens": 1024,
            "messages": [{"role": "user", "content": prompt}],
        },
        timeout=90,
    )
    response.raise_for_status()
    payload = response.json()
    blocks = payload.get("content") if isinstance(payload, dict) else None
    if not isinstance(blocks, list):
        raise ValueError("Missing content")
    text = "".join(
        block.get("text", "") for block in blocks
        if isinstance(block, dict) and block.get("type") == "text"
    ).strip()
    if not text:
        raise ValueError("Missing text")
    return text


def ask_gpt(prompt, model=None, api_key=None, base_url=None, protocol="openai"):
    model = model or "deepseek-v4-flash"
    api_key = api_key or ""
    base_url = base_url or "https://api.deepseek.com"
    if not model or not base_url:
        raise AIRequestError("模型和 Base URL 不能为空。")
    if not api_key and "localhost" not in base_url and "127.0.0.1" not in base_url:
        raise AIRequestError("当前配置没有 API Key。")
    try:
        if protocol == "anthropic":
            return _anthropic_chat(prompt, model, api_key, base_url)
        if protocol != "openai":
            raise ValueError("Unsupported protocol")
        client = OpenAI(api_key=api_key or "ollama", base_url=base_url)
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
        )
        text = response.choices[0].message.content
        if not isinstance(text, str) or not text.strip():
            raise ValueError("Missing text")
        return text
    except AIRequestError:
        raise
    except Exception as error:
        raise AIRequestError(f"请求失败（{type(error).__name__}），请检查接口、模型和密钥。") from None
