"""Project monitoring. A project is checked only when its own read succeeds."""

from __future__ import annotations

import subprocess
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from osiris.audit.redact import redact_text
from osiris.db.database import Database
from osiris.integrations.github_adapter import GitHubService
from osiris.util import new_id, utc_iso

_GIT_TIMEOUT = 15


class ProjectMonitor:
    def __init__(self, db: Database, github: GitHubService, url_mode: str = "mock") -> None:
        self.db = db
        self.github = github
        self.url_mode = url_mode if url_mode in {"mock", "dry_run", "live"} else "mock"

    def list_projects(self) -> list[dict]:
        return self.db.list_projects()

    def add_project(self, name: str, kind: str, location: str) -> dict:
        if kind not in {"local_git", "github", "url"}:
            raise ValueError("Project type must be local_git, github, or url.")
        name = name.strip()
        location = location.strip()
        if not name or not location:
            raise ValueError("A project needs a name and a location.")
        if kind == "local_git":
            path = Path(location).expanduser()
            if not path.is_dir():
                raise ValueError("The local project folder does not exist.")
            location = str(path.resolve())
        record = {
            "id": new_id(),
            "name": name,
            "kind": kind,
            "location": location,
            "enabled": True,
            "created_at": utc_iso(),
        }
        self.db.insert_project(record)
        self.db.add_audit("projects", "add", name, "saved", kind)
        return record

    def remove_project(self, project_id: str) -> None:
        self.db.delete_project(project_id)
        self.db.add_audit("projects", "remove", project_id, "removed", "")

    def check_all(self) -> list[dict]:
        results = []
        for project in self.db.list_projects():
            if not project["enabled"]:
                continue
            results.append(self.check_project(project))
        return results

    def check_project(self, project: dict) -> dict:
        kind = project.get("kind")
        base = {
            "id": project.get("id", ""),
            "name": project.get("name", "Project"),
            "kind": kind,
            "location": project.get("location", ""),
        }
        if kind == "local_git":
            return {**base, **self._check_local(project["location"])}
        if kind == "github":
            return {**base, **self._check_github(project["location"])}
        if kind == "url":
            return {**base, **self._check_url(project["location"])}
        return {
            **base,
            "ok": False,
            "live_check": False,
            "source": "invalid",
            "error": "This project type is not supported.",
            "summary": "Not checked.",
            "commits": [],
            "issues": [],
            "changes": [],
            "tests": {"state": "unavailable", "detail": "No test report was read."},
            "tasks": [],
        }

    def _check_local(self, location: str) -> dict:
        root = Path(location).expanduser()
        try:
            root = root.resolve(strict=True)
        except OSError:
            return self._failed("The project folder does not exist.")
        try:
            inside = self._git(root, ["rev-parse", "--is-inside-work-tree"])
        except Exception as error:
            return self._failed(f"Git could not read this folder. {error}")
        if inside.strip() != "true":
            return self._failed("This folder is not a Git work tree.")
        try:
            log_text = self._git(root, ["log", "-5", "--pretty=format:%h%x09%s"])
            status = self._git(root, ["status", "--porcelain"])
        except Exception as error:
            return self._failed(f"Git status failed. {error}")
        commits = []
        for line in log_text.splitlines():
            if "\t" not in line:
                continue
            sha, message = line.split("\t", 1)
            commits.append({"sha": sha, "message": message, "simulated": False})
        changes = [line for line in status.splitlines() if line.strip()]
        tasks = self._local_tasks(root)
        tests = self._local_tests(root)
        summary = f"{len(commits)} recent commits, {len(changes)} local changes."
        return {
            "ok": True,
            "live_check": True,
            "source": "git",
            "error": None,
            "summary": summary,
            "commits": commits,
            "issues": [],
            "changes": changes,
            "tests": tests,
            "tasks": tasks,
        }

    def _check_github(self, repository: str) -> dict:
        commits = self.github.inspect_commits(repository)
        issues = self.github.inspect_issues(repository)
        live = bool(commits.get("live_check") and issues.get("live_check"))
        if not live:
            reason = commits.get("error") or commits.get("summary") or "GitHub was not checked."
            return {
                "ok": False,
                "live_check": False,
                "source": commits.get("source", "unavailable"),
                "error": None if commits.get("source") in {"mock", "dry_run"} else reason,
                "summary": "Not checked. " + commits.get("summary", reason),
                "commits": [],
                "issues": [],
                "changes": [],
                "preview": {"commits": commits.get("items", []), "issues": issues.get("items", [])},
                "tests": {"state": "unavailable", "detail": "Build status was not checked."},
                "tasks": [],
            }
        return {
            "ok": True,
            "live_check": True,
            "source": "github",
            "error": None,
            "summary": f"{commits.get('summary', '')} {issues.get('summary', '')}".strip(),
            "commits": commits.get("items", []),
            "issues": issues.get("items", []),
            "changes": [],
            "tests": {"state": "unavailable", "detail": "Workflow runs were not requested."},
            "tasks": [item.get("title", "") for item in issues.get("items", [])],
        }

    def _check_url(self, url: str) -> dict:
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return self._failed("Only http and https URLs can be checked.")
        if self.url_mode != "live":
            return {
                "ok": False,
                "live_check": False,
                "source": self.url_mode,
                "error": None,
                "summary": "Not checked. URL monitoring is simulated and did not open the address.",
                "commits": [],
                "issues": [],
                "changes": [],
                "tests": {"state": "unavailable", "detail": "No remote build was read."},
                "tasks": [],
            }
        request = urllib.request.Request(url, method="GET", headers={"User-Agent": "OsirisMonitor"})
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                status = getattr(response, "status", 0)
                response.read(256)
        except Exception as error:
            return self._failed(redact_text(str(error)))
        return {
            "ok": True,
            "live_check": True,
            "source": "url",
            "error": None,
            "summary": f"HTTP {status}.",
            "http_status": status,
            "commits": [],
            "issues": [],
            "changes": [],
            "tests": {"state": "unavailable", "detail": "A URL check does not include test results."},
            "tasks": [],
        }

    def _local_tasks(self, root: Path) -> list[str]:
        path = root / "TASKS.md"
        if not path.is_file():
            return []
        tasks = []
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            stripped = line.strip()
            if stripped.startswith("- [ ]"):
                tasks.append(stripped[5:].strip())
        return tasks[:20]

    def _local_tests(self, root: Path) -> dict:
        path = root / "osiris-test-report.json"
        if not path.is_file():
            return {"state": "not_configured", "detail": "No osiris-test-report.json file is configured."}
        try:
            import json

            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"state": "unavailable", "detail": "The test report file could not be read."}
        failed = payload.get("failed") if isinstance(payload, dict) else None
        if not isinstance(failed, int):
            return {"state": "unavailable", "detail": "The test report did not include a failed count."}
        detail = str(payload.get("summary", ""))[:300]
        state = "failed" if failed else "passed"
        return {"state": state, "failed": failed, "detail": detail}

    def _git(self, root: Path, args: list[str]) -> str:
        completed = subprocess.run(
            ["git", *args],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT,
            check=False,
            shell=False,
        )
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "git failed").strip()
            raise RuntimeError(detail[:300])
        return completed.stdout

    def _failed(self, error: str) -> dict:
        return {
            "ok": False,
            "live_check": False,
            "source": "error",
            "error": error,
            "summary": "Not checked.",
            "commits": [],
            "issues": [],
            "changes": [],
            "tests": {"state": "unavailable", "detail": "Tests were not read because the check failed."},
            "tasks": [],
        }
