"""One-time migration from project-local files used by older releases."""

import ast
from dataclasses import dataclass, field
import hashlib
import os
from pathlib import Path
import shutil
import sys
import tempfile

from utils.app_paths import records_dir
from utils.llm_profiles import PRESETS, ProfileStoreError, load_profile_state, save_profile


@dataclass
class MigrationResult:
    records_moved: int = 0
    env_migrated: bool = False
    warnings: list[str] = field(default_factory=list)


def legacy_root():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def _file_digest(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.digest()


def _unused_target(target):
    if not target.exists():
        return target
    stem, suffix = target.stem, target.suffix
    for index in range(1, 10_000):
        candidate = target.with_name(f"{stem}_migrated_{index}{suffix}")
        if not candidate.exists():
            return candidate
    raise OSError("无法为迁移记录分配文件名。")


def migrate_records(source_dir, destination=None):
    source_dir = Path(source_dir)
    destination = Path(destination) if destination else records_dir()
    if not source_dir.is_dir() or source_dir.resolve() == destination.resolve():
        return 0
    moved = 0
    destination.mkdir(parents=True, exist_ok=True)
    for source in sorted(source_dir.glob("eat_record_*.json")):
        target = destination / source.name
        if target.exists() and _file_digest(source) == _file_digest(target):
            source.unlink()
            moved += 1
            continue
        target = _unused_target(target)
        fd, temporary_name = tempfile.mkstemp(
            prefix=f".{target.stem}_", suffix=".tmp", dir=destination,
        )
        os.close(fd)
        try:
            shutil.copy2(source, temporary_name)
            temporary = Path(temporary_name)
            if _file_digest(source) != _file_digest(temporary):
                raise OSError("迁移后的记录校验失败。")
            temporary.replace(target)
            source.unlink()
            moved += 1
        finally:
            try:
                Path(temporary_name).unlink()
            except OSError:
                pass
    try:
        source_dir.rmdir()
    except OSError:
        pass
    return moved


def _read_legacy_env(path):
    values = {}
    for raw_line in Path(path).read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if key not in {"API_KEY", "BASE_URL", "MODEL"}:
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            try:
                value = ast.literal_eval(value)
            except (SyntaxError, ValueError):
                value = value[1:-1]
        values[key] = str(value).strip()
    return values


def _provider_for(base_url):
    normalized = base_url.rstrip("/").casefold()
    for provider, preset in PRESETS.items():
        if preset["base_url"].rstrip("/").casefold() == normalized:
            return provider
    return "DeepSeek"


def migrate_env(env_path):
    env_path = Path(env_path)
    if not env_path.is_file():
        return False
    values = _read_legacy_env(env_path)
    base_url = values.get("BASE_URL") or PRESETS["DeepSeek"]["base_url"]
    model = values.get("MODEL") or PRESETS["DeepSeek"]["model"]
    api_key = values.get("API_KEY", "")
    provider = _provider_for(base_url)
    state = load_profile_state()
    existing = next(
        (item for item in state["profiles"] if item["name"] == "从旧版 .env 迁移"),
        None,
    )
    save_profile(
        existing["id"] if existing else None,
        "从旧版 .env 迁移", provider, base_url, model, api_key,
    )
    env_path.unlink()
    return True


def migrate_legacy_files(root=None):
    root = Path(root) if root else legacy_root()
    result = MigrationResult()
    try:
        result.records_moved = migrate_records(root / "eat_records")
    except OSError:
        result.warnings.append("旧版消费记录未能全部迁移，请检查稳定数据目录权限。")
    try:
        result.env_migrated = migrate_env(root / ".env")
    except (OSError, ProfileStoreError):
        result.warnings.append(
            "旧版 .env 尚未删除：配置或 API Key 未能安全迁移，请检查系统凭据库。"
        )
    return result
