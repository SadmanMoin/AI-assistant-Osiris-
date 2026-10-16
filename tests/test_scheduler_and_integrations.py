import shutil
import subprocess
import sys
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))
import support

from osiris.agent.planner import plan_with_rules
from osiris.integrations.github_adapter import GitHubService
from osiris.reporting import summarize_checks
from osiris.scheduler.service import SchedulerService, compute_next
from osiris.util import new_id, utc_iso


class SchedulerTests(unittest.TestCase):
    def setUp(self):
        self.app, self.folder = support.make_app()

    def tearDown(self):
        support.cleanup(self.folder)

    def _due(self, calls, retry_limit=2):
        schedule = self.app.scheduler.add(
            automation_id="sys-project-monitor",
            name="Morning check",
            cadence="interval",
            interval_minutes=60,
            retry_limit=retry_limit,
            parameters={},
        )
        past = utc_iso(datetime.now(timezone.utc) - timedelta(minutes=1))
        with self.app.db.session() as conn:
            conn.execute("UPDATE schedules SET next_run_at = ? WHERE id = ?", (past, schedule["id"]))
        service = SchedulerService(self.app.db, calls, "Asia/Dhaka")
        return service, schedule["id"]

    def test_schedule_persists_across_a_new_service(self):
        seen = []

        def execute(schedule):
            seen.append(schedule["id"])
            return {"status": "completed", "summary": "done"}

        service, schedule_id = self._due(execute)
        results = service.tick()
        self.assertEqual(results[0]["status"], "completed")
        self.assertEqual(seen, [schedule_id])
        again = SchedulerService(self.app.db, execute, "Asia/Dhaka")
        self.assertEqual(again.tick(), [])

    def test_overlap_does_not_start_a_second_run(self):
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

        def execute(schedule):
            raise AssertionError("overlap should not execute")

        service, _ = self._due(execute)
        results = service.tick()
        self.assertEqual(results[0]["status"], "overlap")
        running = [row for row in self.app.db.list_executions() if row["status"] == "running"]
        self.assertEqual(len(running), 1)

    def test_retry_then_success(self):
        state = {"n": 0}

        def execute(schedule):
            state["n"] += 1
            if state["n"] == 1:
                return {"status": "failed", "summary": "temporary"}
            return {"status": "completed", "summary": "ok"}

        service, schedule_id = self._due(execute, retry_limit=2)
        moment = datetime.now(timezone.utc)
        first = service.tick(moment)
        self.assertEqual(first[0]["status"], "retry")
        second = service.tick(moment + timedelta(minutes=5))
        self.assertEqual(second[0]["status"], "completed")
        stored = self.app.db.get_schedule(schedule_id)
        self.assertEqual(stored["retry_count"], 0)
        self.assertEqual(stored["last_status"], "completed")

    def test_disabled_schedule_and_emergency_stop(self):
        def execute(schedule):
            raise AssertionError("should not run")

        service, schedule_id = self._due(execute)
        self.app.scheduler.set_enabled(schedule_id, False)
        self.assertEqual(service.tick(), [])
        self.app.scheduler.set_enabled(schedule_id, True)
        self.app.stop_all()
        self.assertEqual(service.tick(), [])

    def test_daily_next_run_uses_the_timezone(self):
        after = datetime(2026, 10, 16, 4, 0, tzinfo=timezone.utc)  # 10:00 in Dhaka
        nxt = compute_next(
            "daily",
            after=after,
            time_of_day="08:00",
            days=None,
            interval_minutes=None,
            timezone_name="Asia/Dhaka",
        )
        local = nxt.astimezone(ZoneInfo("Asia/Dhaka"))
        self.assertEqual((local.year, local.month, local.day, local.hour, local.minute), (2026, 10, 17, 8, 0))


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.app, self.folder = support.make_app()

    def tearDown(self):
        support.cleanup(self.folder)

    def test_mock_github_does_not_use_the_network_and_is_not_a_live_check(self):
        def boom(*args, **kwargs):
            raise AssertionError("network was used")

        with patch("urllib.request.urlopen", boom):
            commits = self.app.github.inspect_commits("octocat/hello")
        self.assertFalse(commits["live_check"])
        self.assertEqual(commits["source"], "mock")
        self.app.projects.add_project("Site", "github", "octocat/hello")
        summary = summarize_checks(self.app.projects.check_all())
        self.assertIn("not checked", summary)
        self.assertNotIn(": checked", summary)

    def test_live_github_without_a_token_is_unavailable(self):
        self.app.set_integration_mode("github", "live")
        os_token = __import__("os").environ.pop("GITHUB_TOKEN", None)
        try:
            with patch("urllib.request.urlopen", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("network"))):
                result = self.app.github.inspect_commits("octocat/hello")
        finally:
            if os_token is not None:
                __import__("os").environ["GITHUB_TOKEN"] = os_token
        self.assertFalse(result["live_check"])
        self.assertIn("GITHUB_TOKEN", result["error"])

    def test_email_injection_cannot_change_the_recipient_or_send(self):
        listing = self.app.email.list_messages()
        injected = next(item for item in listing["messages"] if item["id"] == "msg-inject-002")
        self.assertEqual(injected["priority"], "low")
        reply = self.app.email.suggest_reply(injected["id"])
        self.assertEqual(reply["to"], "news@example.com")
        self.assertNotIn("attacker@evil.example", reply["to"])
        draft = self.app.email.save_draft(
            to=reply["to"],
            subject=reply["subject"],
            body=reply["body"],
            in_reply_to=injected["id"],
        )
        refused = self.app.email.send(
            to="attacker@evil.example",
            subject=draft["subject"],
            body=draft["body"],
            confirmed=True,
            confirmed_to="attacker@evil.example",
            known_recipients={"news@example.com"},
        )
        self.assertFalse(refused["delivered"])
        self.assertEqual(refused["status"], "refused")
        unconfirmed = self.app.email.send(
            to="news@example.com",
            subject="Re: Weekly notes",
            body="Hello",
            confirmed=False,
            confirmed_to="news@example.com",
            known_recipients={"news@example.com"},
        )
        self.assertEqual(unconfirmed["status"], "refused")

    def test_confirmed_mock_send_is_not_delivered_and_duplicates_are_blocked(self):
        first = self.app.email.send(
            to="ayesha@example.com",
            subject="Re: Homepage copy review",
            body="Thank you.",
            confirmed=True,
            confirmed_to="ayesha@example.com",
            known_recipients={"ayesha@example.com"},
        )
        second = self.app.email.send(
            to="ayesha@example.com",
            subject="Re: Homepage copy review",
            body="Thank you.",
            confirmed=True,
            confirmed_to="ayesha@example.com",
            known_recipients={"ayesha@example.com"},
        )
        self.assertFalse(first["delivered"])
        self.assertEqual(first["status"], "not_sent")
        self.assertEqual(second["status"], "duplicate_prevented")

    def test_social_draft_is_not_published_until_a_real_publisher_returns_an_id(self):
        drafts = self.app.social.create_drafts("my latest project", language="en")
        self.assertTrue(drafts)
        self.assertTrue(all(item["published"] is False and item["provider_post_id"] is None for item in drafts))
        draft_id = drafts[0]["id"]
        self.app.social.approve_draft(draft_id)
        result = self.app.social.publish(draft_id, confirmed=True)
        self.assertFalse(result["published"])
        self.assertIsNone(result["provider_post_id"])
        blocked = self.app.social.publish(drafts[1]["id"], confirmed=True)
        self.assertEqual(blocked["status"], "refused")

    def test_live_publisher_records_a_post_id_only_when_returned(self):
        self.app.social.mode = "live"
        self.app.social.live_publisher = lambda draft: {"id": "post-123"}
        drafts = self.app.social.create_drafts("launch note")
        draft_id = drafts[0]["id"]
        self.app.social.approve_draft(draft_id)
        result = self.app.social.publish(draft_id, confirmed=True)
        self.assertTrue(result["published"])
        self.assertEqual(result["provider_post_id"], "post-123")

    def test_local_git_failure_is_not_described_as_checked(self):
        empty = self.folder / "empty-project"
        empty.mkdir()
        self.app.projects.add_project("Empty", "local_git", str(empty))
        summary = summarize_checks(self.app.projects.check_all())
        self.assertIn("Empty: not checked", summary)

    def test_local_git_success_reports_the_commit(self):
        if shutil.which("git") is None:
            self.skipTest("git is not installed")
        repo = self.folder / "repo"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True, capture_output=True)
        (repo / "README.md").write_text("hello", encoding="utf-8")
        subprocess.run(["git", "add", "README.md"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "first update"], cwd=repo, check=True, capture_output=True)
        self.app.projects.add_project("Demo", "local_git", str(repo))
        result = self.app.projects.check_project(self.app.projects.list_projects()[0])
        self.assertTrue(result["live_check"])
        self.assertTrue(any("first update" in item["message"] for item in result["commits"]))

    def test_url_mock_and_live_failure_are_not_checks(self):
        self.app.projects.add_project("Docs", "url", "https://example.com/status")
        mocked = self.app.projects.check_all()[0]
        self.assertFalse(mocked["live_check"])
        self.app.set_integration_mode("web", "live")
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("down")):
            live = self.app.projects.check_all()[0]
        self.assertFalse(live["live_check"])
        summary = summarize_checks([live])
        self.assertIn("not checked", summary)

    def test_assistant_actions_do_not_send_or_publish(self):
        plan = plan_with_rules(
            "Prepare a reply to the latest email from my client.",
            self.app.catalog(),
        )
        result = self.app.perform(plan)
        self.assertIn("No email was sent", result["summary"])
        self.assertFalse(result["draft"]["sent"])
        social = plan_with_rules(
            "Create a professional LinkedIn post about my latest project.",
            self.app.catalog(),
        )
        drafted = self.app.perform(social)
        self.assertIn("Nothing was published", drafted["summary"])
        self.assertTrue(all(item["provider_post_id"] is None for item in drafted["drafts"]))


class RedactionTests(unittest.TestCase):
    def test_patterns_and_exact_values(self):
        from osiris.audit.redact import redact_text

        text = "password=hunter2 bearer abcdefghijkl github_pat_" + ("b" * 22)
        cleaned = redact_text(text, ["exact-secret-value"])
        self.assertNotIn("hunter2", cleaned)
        self.assertNotIn("abcdefghijkl", cleaned)
        self.assertNotIn("github_pat_", cleaned)
        cleaned_exact = redact_text("see exact-secret-value now", ["exact-secret-value"])
        self.assertNotIn("exact-secret-value", cleaned_exact)


if __name__ == "__main__":
    unittest.main()
