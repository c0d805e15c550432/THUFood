"""Persistence and LLM provider tests; all secrets are synthetic."""

import json
import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch

from utils.ask_gpt import AIRequestError, ask_gpt
from utils.app_paths import config_dir, data_dir, records_dir
from utils.auth import TrustedDevice
from utils.legacy_migration import migrate_legacy_files
from utils.llm_profiles import (
    PRESETS,
    delete_profile,
    load_profile_api_key,
    load_profile_state,
    save_profile,
)
from utils.secure_store import delete_secret, get_secret, set_secret
from utils.trusted_store import load_trusted_device, save_trusted_device


class FakeKeyring:
    def __init__(self):
        self.values = {}

    def get_password(self, service, account):
        return self.values.get((service, account))

    def set_password(self, service, account, secret):
        self.values[(service, account)] = secret

    def delete_password(self, service, account):
        self.values.pop((service, account), None)


class SecureStoreTests(unittest.TestCase):
    def test_keyring_round_trip_uses_hashed_identity(self):
        backend = FakeKeyring()
        with patch("utils.secure_store.keyring", backend):
            self.assertTrue(set_secret("Test", "student-account", "synthetic-secret"))
            self.assertEqual(get_secret("Test", "student-account"), "synthetic-secret")
            service, account = next(iter(backend.values))
            self.assertNotIn("student-account", account)
            self.assertNotIn("synthetic-secret", service)
            self.assertTrue(delete_secret("Test", "student-account"))
            self.assertIsNone(get_secret("Test", "student-account"))

    def test_trusted_device_round_trip_and_validation(self):
        backend = FakeKeyring()
        trusted = TrustedDevice("0" * 32, "a" * 32)
        with patch("utils.secure_store.keyring", backend):
            self.assertTrue(save_trusted_device("student-account", trusted))
            self.assertEqual(load_trusted_device("student-account"), trusted)
            raw = next(iter(backend.values.values()))
            self.assertNotIn("student-account", raw)


class AppPathAndMigrationTests(unittest.TestCase):
    def test_directory_overrides_are_separate(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch.dict(os.environ, {
                "THUFOOD_CONFIG_DIR": str(root / "config"),
                "THUFOOD_DATA_DIR": str(root / "data"),
            }):
                self.assertEqual(config_dir(), (root / "config").resolve())
                self.assertEqual(data_dir(), (root / "data").resolve())
                self.assertEqual(records_dir(), (root / "data" / "eat_records").resolve())

    def test_legacy_env_and_records_migrate_without_plaintext_secret(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            legacy = root / "legacy"
            old_records = legacy / "eat_records"
            old_records.mkdir(parents=True)
            (old_records / "eat_record_20250101_120000.json").write_text(
                '{"resultData": {"rows": []}}', encoding="utf-8",
            )
            legacy.mkdir(exist_ok=True)
            (legacy / ".env").write_text(
                'API_KEY="synthetic-migration-key"\n'
                'BASE_URL="https://api.deepseek.com"\n'
                'MODEL="synthetic-model"\n'
                'TEST_MODE=false\n',
                encoding="utf-8",
            )
            config = root / "config"
            data = root / "data"
            with patch.dict(os.environ, {
                "THUFOOD_CONFIG_DIR": str(config),
                "THUFOOD_DATA_DIR": str(data),
            }), patch("utils.llm_profiles.set_secret", return_value=True) as set_secret_mock:
                result = migrate_legacy_files(legacy)

            self.assertEqual(result.records_moved, 1)
            self.assertTrue(result.env_migrated)
            self.assertFalse((legacy / ".env").exists())
            self.assertTrue((data / "eat_records" / "eat_record_20250101_120000.json").is_file())
            metadata = (config / "llm_profiles.json").read_text(encoding="utf-8")
            self.assertNotIn("synthetic-migration-key", metadata)
            set_secret_mock.assert_called_once()


class LLMProfileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.env = patch.dict(os.environ, {"THUFOOD_CONFIG_DIR": self.temp.name})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.secrets = {}
        self.get_patch = patch("utils.llm_profiles.get_secret", side_effect=lambda ns, key: self.secrets.get((ns, key)))
        self.set_patch = patch("utils.llm_profiles.set_secret", side_effect=self._set_secret)
        self.delete_patch = patch("utils.llm_profiles.delete_secret", side_effect=lambda ns, key: self.secrets.pop((ns, key), None) is not None)
        self.get_patch.start(); self.set_patch.start(); self.delete_patch.start()
        self.addCleanup(self.get_patch.stop); self.addCleanup(self.set_patch.stop); self.addCleanup(self.delete_patch.stop)

    def _set_secret(self, namespace, identity, secret):
        self.secrets[(namespace, identity)] = secret
        return True

    def test_multiple_profiles_persist_metadata_and_keys_separately(self):
        first = save_profile(None, "首选", "DeepSeek", "https://api.deepseek.com", "deepseek-v4-flash", "key-one")
        second = save_profile(None, "本地", "Ollama", "http://localhost:11434/v1", "qwen3:8b", "")
        state = load_profile_state()
        self.assertEqual([item["name"] for item in state["profiles"]], ["首选", "本地"])
        self.assertEqual(state["selected"], second["id"])
        self.assertEqual(load_profile_api_key(first["id"]), "key-one")
        disk_text = (Path(self.temp.name) / "llm_profiles.json").read_text(encoding="utf-8")
        self.assertNotIn("key-one", disk_text)
        delete_profile(first["id"])
        self.assertNotIn(first["id"], {item["id"] for item in load_profile_state()["profiles"]})

    def test_requested_provider_presets_are_available(self):
        self.assertEqual(
            set(PRESETS),
            {"OpenAI", "Claude", "Grok", "Ollama", "Gemini", "千问", "智谱", "Kimi", "MiniMax", "并行智算云", "DeepSeek"},
        )
        self.assertEqual(next(iter(PRESETS)), "DeepSeek")


class AIClientTests(unittest.TestCase):
    @patch("utils.ask_gpt.requests.post")
    def test_anthropic_profile_uses_messages_api(self, post):
        response = Mock()
        response.json.return_value = {"content": [{"type": "text", "text": "synthetic answer"}]}
        post.return_value = response
        self.assertEqual(
            ask_gpt("hello", model="claude-test", api_key="secret", base_url="https://api.anthropic.com", protocol="anthropic"),
            "synthetic answer",
        )
        self.assertEqual(post.call_args.args[0], "https://api.anthropic.com/v1/messages")
        self.assertEqual(post.call_args.kwargs["headers"]["x-api-key"], "secret")

    @patch("utils.ask_gpt.OpenAI")
    def test_openai_compatible_profile(self, client_class):
        client_class.return_value.chat.completions.create.return_value = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="synthetic answer"))]
        )
        self.assertEqual(
            ask_gpt("hello", model="model", api_key="secret", base_url="https://example.test/v1"),
            "synthetic answer",
        )

    @patch("utils.ask_gpt.OpenAI", side_effect=RuntimeError("secret-key private body"))
    def test_client_errors_do_not_expose_secrets(self, _client):
        with self.assertRaises(AIRequestError) as error:
            ask_gpt("hello", model="model", api_key="secret-key", base_url="https://example.test/v1")
        self.assertNotIn("secret-key", str(error.exception))
        self.assertNotIn("private body", str(error.exception))


if __name__ == "__main__":
    unittest.main()
