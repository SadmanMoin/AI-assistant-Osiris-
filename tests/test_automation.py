import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import support

from osiris.automation.validator import validate_source
from osiris.errors import ApprovalError, ExecutionError


MALICIOUS = '''
import os
def run(tools, params):
    os.system("echo hi")
    return {"summary": "pwned"}
'''.strip() + "\n"

OPEN_SCRIPT = '''
def run(tools, params):
    open("pwned.txt", "w").write("x")
    return {"summary": "pwned"}
'''.strip() + "\n"

LOOP = '''
def run(tools, params):
    while True:
        pass
'''.strip() + "\n"

HUGE = '''
def run(tools, params):
    return {"summary": "x" * 50000}
'''.strip() + "\n"

SEND_LIE = '''
def run(tools, params):
    tools.email.request_send(to="ayesha@example.com", subject="Hi", body="Hello there", in_reply_to="msg-client-001")
    return {"summary": "I sent the email."}
'''.strip() + "\n"

READ_SCRIPT = '''
def run(tools, params):
    allowed = tools.files.read_text(params["allowed"])
    denied = tools.files.read_text(params["denied"])
    return {"summary": "done", "allowed": allowed, "denied": denied}
'''.strip() + "\n"


class ValidatorTests(unittest.TestCase):
    def test_safe_script_passes(self):
        result = validate_source(support.SAFE_SCRIPT, [])
        self.assertTrue(result["ok"], result["errors"])

    def test_rejects_import_exec_open_and_unknown_tools(self):
        self.assertFalse(validate_source(MALICIOUS, [])["ok"])
        self.assertFalse(validate_source(OPEN_SCRIPT, [])["ok"])
        source = 'def run(tools, params):\n    return tools.os.system("dir")\n'
        result = validate_source(source, [])
        self.assertFalse(result["ok"])

    def test_rejects_missing_permission(self):
        source = 'def run(tools, params):\n    return tools.email.list_messages(limit=1)\n'
        result = validate_source(source, [])
        self.assertFalse(result["ok"])
        self.assertIn("email.read", " ".join(result["errors"]))

    def test_rejects_dunder_access(self):
        source = 'def run(tools, params):\n    return tools.__class__.__name__\n'
        self.assertFalse(validate_source(source, [])["ok"])


class RunnerAndLibraryTests(unittest.TestCase):
    def setUp(self):
        self.app, self.folder = support.make_app()
        self.app.settings.execution_timeout_seconds = 15
        self.app.settings.max_output_chars = 20000

    def tearDown(self):
        support.cleanup(self.folder)

    def test_system_scripts_are_installed_and_searchable(self):
        found = self.app.library.search("github")
        self.assertTrue(any(item["id"] == "sys-github-updates" for item in found))
        source = self.app.library.read_source("sys-project-monitor", 1)
        self.assertTrue(source["matches"])
        self.assertIn("def run", source["source"])

    def test_malicious_script_cannot_be_approved_or_executed(self):
        created = self.app.library.create(
            name="Bad",
            description="no",
            source=MALICIOUS,
            permissions=[],
        )
        version = created["version"]
        self.assertFalse(version["validation"]["ok"])
        with self.assertRaises(ApprovalError):
            self.app.library.approve(created["automation"]["id"], version["version"])
        result = self.app.runner.run(source=OPEN_SCRIPT, permissions=[], params={})
        self.assertEqual(result["status"], "rejected")
        self.assertIsNone(result["execution_id"])
        self.assertNotIn("pwned", result["summary"])

    def test_approve_requires_a_passing_test_and_run_is_isolated(self):
        created = self.app.library.create(
            name="Safe note",
            description="A local note",
            source=support.SAFE_SCRIPT,
            permissions=[],
        )
        automation_id = created["automation"]["id"]
        version = created["version"]["version"]
        self.assertTrue(created["version"]["test_results"]["ok"])
        approved = self.app.library.approve(automation_id, version)
        self.assertEqual(approved["status"], "approved")
        result = self.app.library.run(automation_id, params={})
        self.assertTrue(result["ok"])
        self.assertIn("Library test finished.", result["summary"])

    def test_new_version_does_not_overwrite_the_approved_file(self):
        created = self.app.library.create(
            name="Versioned",
            description="",
            source=support.SAFE_SCRIPT,
            permissions=[],
        )
        automation_id = created["automation"]["id"]
        self.app.library.approve(automation_id, 1)
        approved_path = self.folder / "automation" / "approved" / automation_id / "v1.py"
        original = approved_path.read_text(encoding="utf-8")
        replacement = support.SAFE_SCRIPT.replace("Library test finished.", "Changed text.")
        self.app.library.add_version(automation_id, source=replacement, permissions=[])
        self.assertEqual(approved_path.read_text(encoding="utf-8"), original)
        current = self.app.db.get_automation(automation_id)
        self.assertEqual(current["current_version"], 1)
        self.app.library.approve(automation_id, 2)
        self.app.library.rollback(automation_id, 1)
        self.assertEqual(self.app.db.get_automation(automation_id)["current_version"], 1)
        self.assertTrue((self.folder / "automation" / "approved" / automation_id / "v2.py").is_file())

    def test_tamper_and_disable_and_archive_block_runs(self):
        created = self.app.library.create(
            name="Guarded",
            description="",
            source=support.SAFE_SCRIPT,
            permissions=[],
        )
        automation_id = created["automation"]["id"]
        self.app.library.approve(automation_id, 1)
        path = Path(self.app.db.get_version(automation_id, 1)["script_path"])
        path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        with self.assertRaises(ExecutionError):
            self.app.library.run(automation_id)
        path.write_text(support.SAFE_SCRIPT, encoding="utf-8")
        self.app.library.set_enabled(automation_id, False)
        with self.assertRaises(ExecutionError):
            self.app.library.run(automation_id)
        self.app.library.set_enabled(automation_id, True)
        self.app.library.archive(automation_id)
        with self.assertRaises(ExecutionError):
            self.app.library.run(automation_id)

    def test_timeout_stops_a_loop(self):
        result = self.app.runner.run(
            source=LOOP,
            permissions=[],
            params={},
            instruction_budget=0,
            timeout_seconds=2,
            name="loop",
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "failed")

    def test_worker_environment_does_not_receive_parent_secrets(self):
        os.environ["OSIRIS_SECRET_CANARY"] = "canary-secret-value"
        try:
            from osiris.automation.runner import clean_environment
            from osiris.app import PROJECT_ROOT

            env = clean_environment(PROJECT_ROOT)
            self.assertNotIn("OSIRIS_SECRET_CANARY", env)
            self.assertNotIn("GITHUB_TOKEN", env)
            result = self.app.runner.run(source=support.SAFE_SCRIPT, permissions=[], params={}, name="env")
            log = Path(result["log_path"]).read_text(encoding="utf-8")
        finally:
            os.environ.pop("OSIRIS_SECRET_CANARY", None)
        self.assertNotIn("canary-secret-value", log)

    def test_huge_output_is_truncated(self):
        self.app.settings.max_output_chars = 8000
        result = self.app.runner.run(source=HUGE, permissions=[], params={}, name="huge")
        self.assertTrue(result["truncated"])
        self.assertLess(len(result["summary"]), 8000)

    def test_file_reads_stay_inside_configured_directories(self):
        allowed = self.folder / "allowed"
        secret = self.folder / "secret"
        allowed.mkdir()
        secret.mkdir()
        (allowed / "note.txt").write_text("safe-content", encoding="utf-8")
        (secret / "note.txt").write_text("secret-content", encoding="utf-8")
        self.app.settings.allowed_directories = [str(allowed)]
        result = self.app.runner.run(
            source=READ_SCRIPT,
            permissions=["files.read"],
            params={"allowed": str(allowed / "note.txt"), "denied": str(secret / "note.txt")},
            name="files",
        )
        self.assertTrue(result["ok"], result["error"])
        self.assertIn("safe-content", result["script_summary"] + str(result["tool_calls"]))
        blob = result["summary"] + result["script_summary"] + str(result["tool_calls"])
        self.assertNotIn("secret-content", blob)

    def test_send_request_is_not_delivered_and_cannot_claim_success(self):
        result = self.app.runner.run(
            source=SEND_LIE,
            permissions=["email.send"],
            params={},
            name="send-lie",
        )
        self.assertIn("No email was sent", result["summary"])
        self.assertNotIn("I sent the email", result["summary"])
        self.assertEqual(self.app.db.list_email_drafts(), [])
        self.assertTrue(result["action_requests"])

    def test_emergency_stop_blocks_execution(self):
        self.app.stop_all()
        with self.assertRaises(ExecutionError):
            self.app.runner.run(source=support.SAFE_SCRIPT, permissions=[], params={})
        self.app.resume()
        result = self.app.runner.run(source=support.SAFE_SCRIPT, permissions=[], params={}, name="after")
        self.assertTrue(result["ok"])

    def test_duplicate_run_is_rejected(self):
        from osiris.util import new_id, utc_iso

        self.app.db.begin_execution(
            {
                "id": new_id(),
                "automation_id": "sys-project-monitor",
                "name": "Daily project monitoring",
                "version": 1,
                "trigger": "manual",
                "started_at": utc_iso(),
            }
        )
        with self.assertRaises(ExecutionError):
            self.app.library.run("sys-project-monitor")


if __name__ == "__main__":
    unittest.main()
