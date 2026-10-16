import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import support

from osiris.chat import SYSTEM_PROMPT, ChatSession, escape_html
from osiris.config import Settings
from osiris.llm.client import LMStudioClient


class FakeTransport:
    def __init__(self, models=None, replies=None, errors=None):
        self.models = ["qwen2.5"] if models is None else list(models)
        self.replies = list(replies or ["hello"])
        self.errors = list(errors or [])
        self.model_calls = 0
        self.chats = []

    def list_models(self, timeout=None):
        self.model_calls += 1
        if self.errors:
            raise self.errors.pop(0)
        return list(self.models)

    def chat(self, model, messages, temperature):
        self.chats.append({"model": model, "messages": messages, "temperature": temperature})
        if self.errors:
            raise self.errors.pop(0)
        if not self.replies:
            return ""
        return self.replies.pop(0)


class ChatTests(unittest.TestCase):
    def test_system_prompt_and_reset(self):
        session = ChatSession()
        self.assertEqual(session.messages[0]["content"], SYSTEM_PROMPT)
        session.add_user("hi")
        session.add_assistant("hello")
        session.reset()
        self.assertEqual(session.messages, [{"role": "system", "content": SYSTEM_PROMPT}])

    def test_failed_turn_is_removed(self):
        session = ChatSession()
        session.add_user("hello")
        session.fail_last_turn()
        self.assertEqual(len(session.messages), 1)

    def test_escape_html(self):
        self.assertEqual(escape_html('<b>a & b</b>'), "&lt;b&gt;a &amp; b&lt;/b&gt;")


class LLMTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.mkdtemp(prefix="osiris-llm-")
        self.settings = Settings(lm_base_url="http://localhost:1234/v1", lm_model="", max_retries=2, timeout_seconds=5)
        self.settings.timezone = "UTC"

    def tearDown(self):
        support.cleanup(Path(self._tmpdir))

    def test_retries_connection_errors_then_succeeds(self):
        transport = FakeTransport(errors=[ConnectionError("down"), ConnectionError("down")])
        client = LMStudioClient(self.settings, transport)
        models = client.list_models()
        self.assertEqual(models, ["qwen2.5"])
        self.assertEqual(transport.model_calls, 3)

    def test_retry_limit_is_enforced(self):
        transport = FakeTransport(errors=[ConnectionError("a"), ConnectionError("b"), ConnectionError("c")])
        client = LMStudioClient(self.settings, transport)
        with self.assertRaises(ConnectionError):
            client.list_models()
        self.assertEqual(transport.model_calls, 3)

    def test_non_retryable_error_is_not_retried(self):
        transport = FakeTransport(errors=[RuntimeError("bad request")])
        client = LMStudioClient(self.settings, transport)
        with self.assertRaises(RuntimeError):
            client.list_models()
        self.assertEqual(transport.model_calls, 1)

    def test_blank_model_uses_the_first_listed_model(self):
        transport = FakeTransport(models=["local-qwen", "other"], replies=["Answer"])
        client = LMStudioClient(self.settings, transport)
        answer = client.complete_chat([{"role": "user", "content": "Hi"}])
        self.assertEqual(answer, "Answer")
        self.assertEqual(transport.chats[0]["model"], "local-qwen")
        self.assertEqual(transport.chats[0]["temperature"], 0.7)

    def test_configured_model_is_sent(self):
        self.settings.lm_model = "qwen2.5-instruct"
        transport = FakeTransport(replies=["ok"])
        client = LMStudioClient(self.settings, transport)
        client.complete_chat([{"role": "user", "content": "Hi"}])
        self.assertEqual(transport.chats[0]["model"], "qwen2.5-instruct")
        self.assertEqual(transport.model_calls, 0)

    def test_empty_model_list_uses_the_original_message(self):
        transport = FakeTransport(models=[])
        client = LMStudioClient(self.settings, transport)
        status = client.check_connection()
        self.assertFalse(status["ok"])
        self.assertIn("কোনো মডেল", status["error"])

    def test_connection_error_redacts_secrets(self):
        token = "ghp_" + ("a" * 24)
        os.environ["GITHUB_TOKEN"] = token
        try:
            transport = FakeTransport(errors=[RuntimeError(f"failed token={token}")])
            client = LMStudioClient(self.settings, transport)
            status = client.check_connection()
        finally:
            os.environ.pop("GITHUB_TOKEN", None)
        self.assertNotIn(token, status["error"])
        self.assertIn("[REDACTED]", status["error"])

    def test_settings_file_does_not_store_the_api_key(self):
        os.environ["OSIRIS_LM_API_KEY"] = "sk-live-secret-value"
        try:
            path = Path(self._tmpdir) / "settings.json"
            self.settings.save(path)
            text = path.read_text(encoding="utf-8")
        finally:
            os.environ.pop("OSIRIS_LM_API_KEY", None)
        self.assertNotIn("sk-live-secret-value", text)
        self.assertNotIn("api_key", text)


if __name__ == "__main__":
    unittest.main()
