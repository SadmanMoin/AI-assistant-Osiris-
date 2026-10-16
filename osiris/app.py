"""Wires the assistant, library, runner, and integrations."""

from __future__ import annotations

import os
from datetime import timedelta
from pathlib import Path

from osiris.agent.planner import Planner
from osiris.automation.manager import AutomationManager
from osiris.automation.runner import Runner
from osiris.chat import ChatSession
from osiris.config import Settings
from osiris.db.database import Database
from osiris.errors import ExecutionError, OsirisError
from osiris.integrations.email_adapter import EmailService
from osiris.integrations.github_adapter import GitHubService, status_text as github_status
from osiris.integrations.projects import ProjectMonitor
from osiris.integrations.social import SocialService
from osiris.llm.client import LMStudioClient
from osiris.reporting import summarize_checks
from osiris.scheduler.service import SchedulerService
from osiris.util import new_id, utc_iso, utcnow

PROJECT_ROOT = Path(__file__).resolve().parents[1]

_INTEGRATIONS = (
    ("email", "Email"),
    ("github", "GitHub"),
    ("social", "Social media"),
    ("web", "Project URLs"),
)


class OsirisApp:
    def __init__(self, root: Path | None = None) -> None:
        self.root = Path(root) if root is not None else PROJECT_ROOT
        self.settings_path = self.root / "data" / "settings.json"
        if self.settings_path.exists():
            self.settings = Settings.load(self.settings_path)
        else:
            self.settings = Settings()
            self.settings.save(self.settings_path)
        self.db = Database(self.root / "data" / "osiris.db")
        self.llm = LMStudioClient(self.settings)
        self.chat = ChatSession()
        self.planner = Planner(self.llm)
        self._seed_integrations()
        self.email = EmailService(self.db, self.integration_mode("email"))
        self.github = GitHubService(self.integration_mode("github"))
        self.projects = ProjectMonitor(self.db, self.github, url_mode=self.integration_mode("web"))
        self.social = SocialService(self.db, self.integration_mode("social"))
        self.runner = Runner(
            project_root=PROJECT_ROOT,
            settings=self.settings,
            db=self.db,
            snapshot_builder=self.build_snapshot,
            logs_dir=self.root / "data" / "logs",
        )
        self.library = AutomationManager(self.root / "automation", self.db, self.runner)
        self.library.ensure_system()
        self.scheduler = SchedulerService(self.db, self.run_scheduled, self.settings.timezone)
        self.db.reconcile_stale_runs(utc_iso(utcnow() - timedelta(hours=2)))

    def integration_mode(self, provider: str) -> str:
        row = self.db.get_integration(provider)
        if row and row["mode"] in {"mock", "dry_run", "live"}:
            return row["mode"]
        return "mock"

    def set_integration_mode(self, provider: str, mode: str) -> None:
        if mode not in {"mock", "dry_run", "live"}:
            raise ValueError("Mode must be mock, dry_run, or live.")
        names = dict(_INTEGRATIONS)
        if provider not in names:
            raise ValueError("Unknown integration.")
        self.db.upsert_integration(provider, names[provider], mode, self._status_line(provider, mode))
        self._rebuild_services()
        self.db.add_audit("integrations", "set_mode", provider, mode, "Mode changed. Passwords are not stored.")

    def save_settings(self, settings: Settings) -> None:
        settings.save(self.settings_path)
        self.settings = settings
        self.llm = LMStudioClient(settings)
        self.planner = Planner(self.llm)
        self.runner.settings = settings
        self.scheduler.timezone_name = settings.timezone
        self.db.add_audit("settings", "save", "settings", "saved", "Secrets were not written to the settings file.")

    def catalog(self) -> list[dict]:
        items = []
        for record in self.library.search(""):
            items.append(
                {
                    "id": record["id"],
                    "name": record["name"],
                    "description": record["description"],
                    "permissions": record["permissions"],
                    "status": record["status"],
                    "enabled": record["enabled"],
                }
            )
        return items

    def prepare_turn(self, text: str) -> dict:
        """Plan a command, or signal that the normal chat path should answer."""
        plan = self.planner.plan(text, self.catalog(), self.chat.transcript())
        if plan.get("route") == "chat":
            return {"kind": "chat"}
        notice = plan.get("notice")
        reply = plan.get("reply") or ""
        if notice:
            reply = notice + "\n\n" + reply
        return {"kind": "plan", "plan": plan, "reply": reply}

    def answer_chat(self) -> str:
        return self.llm.complete_chat(self.chat.messages)

    def perform(self, plan: dict, params: dict | None = None) -> dict:
        """Carry out a reviewed plan. Sends and publishes are not done here."""
        if self.db.get_flag("emergency_stop") == "1" and plan.get("intent") != "list_failed":
            raise ExecutionError("Automations are stopped.")
        intent = plan.get("intent")
        params = dict(params or {})
        if intent == "list_failed":
            return self._list_failed()
        if intent == "monitor_projects":
            checks = self.projects.check_all()
            summary = summarize_checks(checks)
            self._record_direct("Project check", summary, "completed")
            return {"ok": True, "status": "completed", "summary": summary, "data": checks}
        if intent == "draft_email":
            return self._draft_email(plan)
        if intent == "draft_social":
            topic = str(params.get("topic") or plan.get("parameters", {}).get("topic") or "a project update")
            drafts = self.social.create_drafts(
                topic,
                guidelines=self.settings.brand_guidelines,
                preferences=self.settings.content_preferences,
                language=plan.get("language"),
            )
            summary = "Drafts were saved. Nothing was published."
            self._record_direct("Social draft", summary, "completed")
            return {"ok": True, "status": "completed", "summary": summary, "drafts": drafts}
        if intent == "create_script":
            script = plan.get("new_script") or {}
            created = self.library.create(
                name=script.get("name") or "New automation",
                description=script.get("description") or "",
                source=script.get("source") or "",
                permissions=list(script.get("permissions") or []),
                integrations=list(script.get("integrations") or []),
                parameters=script.get("parameters") or {},
            )
            version = created["version"]
            summary = (
                "The script is saved for review and is not enabled."
                if version["validation"].get("ok")
                else "The script was saved as a draft because validation failed. It was not executed."
            )
            return {"ok": version["validation"].get("ok", False), "status": version["status"], "summary": summary, "created": created}
        if intent == "run_automation":
            automation_id = plan.get("automation_id")
            result = self.library.run(automation_id, params=params, trigger="assistant")
            return result
        raise ExecutionError("That plan does not have an action to run.")

    def confirm_send(self, *, to: str, subject: str, body: str, in_reply_to: str, confirmed_to: str, known_recipients: set[str]) -> dict:
        result = self.email.send(
            to=to,
            subject=subject,
            body=body,
            in_reply_to=in_reply_to,
            confirmed=True,
            confirmed_to=confirmed_to,
            known_recipients=known_recipients,
        )
        summary = "No email was sent. " + result.get("detail", "")
        if result.get("delivered"):
            summary = f"Email was sent. Provider id: {result.get('provider_id')}."
        self._record_direct("Email send", summary, "completed" if result.get("delivered") else "failed", "" if result.get("delivered") else result.get("detail", ""))
        return result

    def confirm_publish(self, draft_id: str) -> dict:
        result = self.social.publish(draft_id, confirmed=True)
        if result.get("published"):
            summary = f"Published. Post id: {result.get('provider_post_id')}."
            status = "completed"
        else:
            summary = "Nothing was published. " + result.get("detail", "")
            status = "failed"
        self._record_direct("Social publish", summary, status, "" if result.get("published") else result.get("detail", ""))
        return result

    def stop_all(self) -> None:
        self.db.set_flag("emergency_stop", "1")
        stopped = self.runner.stop_active()
        self.db.add_audit("user", "emergency_stop", "", "stopped", f"Stopped {stopped} running processes.")
        self.db.add_notification("warning", "Automations stopped", "Scheduled and manual runs are blocked until you resume them.")

    def resume(self) -> None:
        self.db.set_flag("emergency_stop", "0")
        self.db.add_audit("user", "resume", "", "resumed", "Automations can run again.")

    def emergency_stopped(self) -> bool:
        return self.db.get_flag("emergency_stop") == "1"

    def build_snapshot(self, permissions: list[str], params: dict, test_mode: bool = False) -> dict:
        allowed = set(permissions)
        snapshot = {
            "projects": [],
            "project_list": [],
            "emails": {
                "ok": True,
                "live": False,
                "messages": [],
                "notice": "Email was not requested by this script.",
            },
            "github": {},
        }
        if "projects.read" in allowed:
            github = GitHubService("mock") if test_mode else self.github
            web_mode = "mock" if test_mode else self.integration_mode("web")
            monitor = ProjectMonitor(self.db, github, url_mode=web_mode)
            snapshot["project_list"] = monitor.list_projects()
            snapshot["projects"] = monitor.check_all()
        if any(name in allowed for name in ("email.read", "email.draft", "email.send")):
            email = EmailService(self.db, "mock") if test_mode else self.email
            snapshot["emails"] = email.list_messages(limit=params.get("limit", 5))
        if "github.read" in allowed:
            github = GitHubService("mock") if test_mode else self.github
            repositories = []
            if params.get("repository"):
                repositories.append(str(params["repository"]))
            for project in self.db.list_projects():
                if project["enabled"] and project["kind"] == "github":
                    repositories.append(project["location"])
            for repository in repositories:
                snapshot["github"][repository] = {
                    "commits": github.inspect_commits(repository, limit=params.get("limit", 5)),
                    "issues": github.inspect_issues(repository, limit=params.get("limit", 5)),
                }
        return snapshot

    def run_scheduled(self, schedule: dict) -> dict:
        return self.library.run(
            schedule["automation_id"],
            params=schedule.get("parameters") or {},
            trigger="schedule",
        )

    def dashboard(self) -> dict:
        local_midnight = utcnow().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
        return self.db.dashboard(utc_iso(local_midnight))

    def _draft_email(self, plan: dict) -> dict:
        listing = self.email.list_messages()
        messages = listing.get("messages") or []
        if not messages:
            summary = listing.get("error") or listing.get("notice") or "No email was read from a connected account."
            self._record_direct("Email draft", summary, "completed")
            return {"ok": True, "status": "completed", "summary": summary, "draft": None, "listing": listing}
        latest = messages[0]
        reply = self.email.suggest_reply(latest["id"], plan.get("language"))
        draft = self.email.save_draft(
            to=reply.get("to") or latest.get("from_address", ""),
            subject=reply.get("subject", ""),
            body=reply.get("body", ""),
            in_reply_to=latest.get("id", ""),
        )
        notice = listing.get("notice") or ""
        summary = "A draft reply was prepared. No email was sent."
        if notice:
            summary = summary + "\n" + notice
        self._record_direct("Email draft", summary, "completed")
        return {"ok": True, "status": "completed", "summary": summary, "draft": draft, "message": latest, "listing": listing}

    def _list_failed(self) -> dict:
        rows = self.db.list_executions(status="failed", limit=50)
        if not rows:
            summary = "No failed automation tasks."
        else:
            lines = []
            for row in rows:
                when = row["started_at"]
                label = row.get("name") or row.get("automation_id") or "Task"
                detail = row.get("error") or row.get("summary") or ""
                lines.append(f"{when}  {label}: {detail}")
            summary = "\n".join(lines)
        return {"ok": True, "status": "completed", "summary": summary, "data": rows}

    def _record_direct(self, name: str, summary: str, status: str, error: str = "") -> None:
        execution_id = new_id()
        self.db.begin_execution(
            {
                "id": execution_id,
                "automation_id": "",
                "name": name,
                "version": None,
                "trigger": "assistant",
                "started_at": utc_iso(),
            }
        )
        self.db.finish_execution(
            execution_id,
            status=status,
            summary=summary,
            error=error,
            log_path="",
            action_requests=[],
        )

    def _seed_integrations(self) -> None:
        for provider, display_name in _INTEGRATIONS:
            if self.db.get_integration(provider) is None:
                self.db.upsert_integration(provider, display_name, "mock", self._status_line(provider, "mock"))

    def _rebuild_services(self) -> None:
        self.email.mode = self.integration_mode("email")
        self.github.mode = self.integration_mode("github")
        self.social.mode = self.integration_mode("social")
        self.projects.github = self.github
        self.projects.url_mode = self.integration_mode("web")

    def _status_line(self, provider: str, mode: str) -> str:
        if provider == "github":
            return github_status(mode)
        if provider == "email":
            if mode == "live":
                if os.environ.get("EMAIL_OAUTH_TOKEN"):
                    return "A token is present, but no OAuth mail sender is connected. Mail will not be sent."
                return "Live mode selected. No authorized email account is connected."
            return "Simulated inbox. No account is contacted."
        if provider == "social":
            if mode == "live":
                return "Live mode selected. No authorized social account is connected, so posts are not published."
            return "Drafts stay on this computer. Nothing is published."
        if mode == "live":
            return "Live URL checks request the address and report the HTTP status only."
        return "URL checks are simulated and do not open the address."


def is_osiris_error(error: Exception) -> bool:
    return isinstance(error, OsirisError)
