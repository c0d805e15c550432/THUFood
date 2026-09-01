"""Saved LLM profile metadata with API keys kept in the OS credential store."""

import json
import os
import tempfile
import uuid

from utils.app_paths import config_dir
from utils.secure_store import delete_secret, get_secret, set_secret


PRESETS = {
    "DeepSeek": {
        "protocol": "openai", "base_url": "https://api.deepseek.com", "model": "deepseek-v4-flash",
    },
    "OpenAI": {
        "protocol": "openai", "base_url": "https://api.openai.com/v1", "model": "gpt-4.1-mini",
    },
    "Claude": {
        "protocol": "anthropic", "base_url": "https://api.anthropic.com", "model": "claude-sonnet-4-6",
    },
    "Grok": {
        "protocol": "openai", "base_url": "https://api.x.ai/v1", "model": "grok-4.6",
    },
    "Ollama": {
        "protocol": "openai", "base_url": "http://localhost:11434/v1", "model": "qwen3.8-27b",
    },
    "Gemini": {
        "protocol": "openai", "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/", "model": "gemini-3.7-flash",
    },
    "千问": {
        "protocol": "openai", "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "model": "qwen-plus",
    },
    "智谱": {
        "protocol": "openai", "base_url": "https://open.bigmodel.cn/api/paas/v4/", "model": "glm-5.2",
    },
    "Kimi": {
        "protocol": "openai", "base_url": "https://api.moonshot.cn/v1", "model": "kimi-k2.6",
    },
    "MiniMax": {
        "protocol": "openai", "base_url": "https://api.minimaxi.com/v1", "model": "MiniMax-M2.7",
    },
    "并行智算云": {
        "protocol": "openai", "base_url": "https://llmapi.paratera.com/v1", "model": "deepseek-v4-flash",
    },
}


class ProfileStoreError(ValueError):
    pass


def profiles_path():
    return config_dir() / "llm_profiles.json"


def _empty_state():
    return {"version": 1, "selected": None, "profiles": []}


def load_profile_state():
    try:
        value = json.loads(profiles_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _empty_state()
    if not isinstance(value, dict) or not isinstance(value.get("profiles"), list):
        return _empty_state()
    profiles = []
    for profile in value["profiles"]:
        if not isinstance(profile, dict):
            continue
        if not all(isinstance(profile.get(key), str) and profile[key].strip()
                   for key in ("id", "name", "provider", "base_url", "model", "protocol")):
            continue
        profiles.append({key: profile[key] for key in (
            "id", "name", "provider", "base_url", "model", "protocol",
        )})
    selected = value.get("selected")
    if selected not in {profile["id"] for profile in profiles}:
        selected = profiles[0]["id"] if profiles else None
    return {"version": 1, "selected": selected, "profiles": profiles}


def _write_state(state):
    target = profiles_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix="llm_profiles_", suffix=".tmp", dir=target.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(state, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, target)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def save_profile(profile_id, name, provider, base_url, model, api_key=""):
    name, base_url, model = name.strip(), base_url.strip(), model.strip()
    if not name or not base_url or not model:
        raise ProfileStoreError("配置名称、Base URL 和模型不能为空。")
    if provider not in PRESETS:
        raise ProfileStoreError("请选择受支持的服务商预设。")
    state = load_profile_state()
    for existing in state["profiles"]:
        if existing["name"].casefold() == name.casefold() and existing["id"] != profile_id:
            raise ProfileStoreError("已存在同名配置。")
    profile_id = profile_id or uuid.uuid4().hex
    if api_key and not set_secret("LLMApiKey", profile_id, api_key):
        raise ProfileStoreError("无法将 API Key 写入系统凭据库，请确认已安装 keyring 且系统凭据服务可用。")
    profile = {
        "id": profile_id,
        "name": name,
        "provider": provider,
        "base_url": base_url,
        "model": model,
        "protocol": PRESETS[provider]["protocol"],
    }
    state["profiles"] = [
        profile if item["id"] == profile_id else item for item in state["profiles"]
    ]
    if not any(item["id"] == profile_id for item in state["profiles"]):
        state["profiles"].append(profile)
    state["selected"] = profile_id
    try:
        _write_state(state)
    except OSError as error:
        raise ProfileStoreError("无法保存配置元数据，请检查用户配置目录是否可写。") from error
    return profile


def delete_profile(profile_id):
    state = load_profile_state()
    state["profiles"] = [item for item in state["profiles"] if item["id"] != profile_id]
    state["selected"] = state["profiles"][0]["id"] if state["profiles"] else None
    try:
        _write_state(state)
    except OSError as error:
        raise ProfileStoreError("无法删除配置，请检查用户配置目录是否可写。") from error
    delete_secret("LLMApiKey", profile_id)


def select_profile(profile_id):
    state = load_profile_state()
    if profile_id in {item["id"] for item in state["profiles"]} and state["selected"] != profile_id:
        state["selected"] = profile_id
        try:
            _write_state(state)
        except OSError:
            # The selected item remains usable for this run even when the
            # preference cannot be persisted.
            pass


def load_profile_api_key(profile_id):
    return get_secret("LLMApiKey", profile_id) or ""
