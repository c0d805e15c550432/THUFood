"""Persist Tsinghua trusted-device material in the OS credential store."""

import json
import re

from utils.auth import TrustedDevice
from utils.secure_store import get_secret, set_secret


_NAMESPACE = "TsinghuaTrustedDevice"


def load_trusted_device(username):
    raw = get_secret(_NAMESPACE, (username or "").strip())
    if not raw:
        return None
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return None
    fingerprint = value.get("fingerprint") if isinstance(value, dict) else None
    token = value.get("token") if isinstance(value, dict) else None
    device_name = value.get("device_name", "THUFood,Python") if isinstance(value, dict) else ""
    if not re.fullmatch(r"[0-9a-fA-F]{32}", fingerprint or ""):
        return None
    if not re.fullmatch(r"[0-9a-fA-F]{32}", token or ""):
        return None
    if not isinstance(device_name, str) or not device_name or len(device_name) > 80:
        return None
    return TrustedDevice(fingerprint, token, device_name)


def save_trusted_device(username, trusted_device):
    username = (username or "").strip()
    if not username or not isinstance(trusted_device, TrustedDevice):
        return False
    if not re.fullmatch(r"[0-9a-fA-F]{32}", trusted_device.fingerprint):
        return False
    if not re.fullmatch(r"[0-9a-fA-F]{32}", trusted_device.token):
        return False
    payload = json.dumps({
        "fingerprint": trusted_device.fingerprint,
        "token": trusted_device.token,
        "device_name": trusted_device.device_name,
    }, ensure_ascii=True, separators=(",", ":"))
    return set_secret(_NAMESPACE, username, payload)
