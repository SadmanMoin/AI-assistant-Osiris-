"""GitHub adapter. Mock and dry-run modes do not open a network connection."""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request

from osiris.audit.redact import redact_text
from osiris.integrations.content import SAMPLE_COMMITS, SAMPLE_ISSUES

_REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


class GitHubService:
    def __init__(self, mode: str = "mock") -> None:
        self.mode = mode if mode in {"mock", "dry_run", "live"} else "mock"

    def inspect_commits(self, repository: str, limit: int = 5) -> dict:
        return self._inspect(repository, "commits", limit)

    def inspect_issues(self, repository: str, limit: int = 5) -> dict:
        return self._inspect(repository, "issues", limit)

    def _inspect(self, repository: str, kind: str, limit: int) -> dict:
        repository = (repository or "").strip()
        if not _REPO.match(repository):
            return self._unavailable(repository, "Repository must look like owner/name.")
        if self.mode != "live":
            preview = SAMPLE_COMMITS if kind == "commits" else SAMPLE_ISSUES
            return {
                "ok": True,
                "live_check": False,
                "source": self.mode,
                "repository": repository,
                "kind": kind,
                "error": None,
                "summary": "Simulated result. The repository was not contacted.",
                "items": preview,
            }
        token = os.environ.get("GITHUB_TOKEN", "").strip()
        if not token:
            return self._unavailable(
                repository,
                "GitHub access is unavailable because GITHUB_TOKEN is not set.",
            )
        per_page = max(1, min(int(limit or 5), 20))
        if kind == "commits":
            url = f"https://api.github.com/repos/{repository}/commits?per_page={per_page}"
        else:
            url = f"https://api.github.com/repos/{repository}/issues?state=open&per_page={per_page}"
        try:
            payload = self._get(url, token)
        except Exception as error:
            return self._unavailable(repository, redact_text(str(error)))
        items = []
        if kind == "commits":
            for entry in payload:
                if not isinstance(entry, dict):
                    continue
                commit = entry.get("commit") or {}
                items.append(
                    {
                        "sha": str(entry.get("sha", ""))[:12],
                        "message": str((commit.get("message") or "").splitlines()[0])[:180],
                        "simulated": False,
                    }
                )
            summary = f"{len(items)} commits fetched from GitHub."
        else:
            for entry in payload:
                if not isinstance(entry, dict) or "pull_request" in entry:
                    continue
                items.append(
                    {
                        "number": entry.get("number"),
                        "title": str(entry.get("title", ""))[:180],
                        "simulated": False,
                    }
                )
            summary = f"{len(items)} open issues fetched from GitHub."
        return {
            "ok": True,
            "live_check": True,
            "source": "github",
            "repository": repository,
            "kind": kind,
            "error": None,
            "summary": summary,
            "items": items,
        }

    def _unavailable(self, repository: str, error: str) -> dict:
        return {
            "ok": False,
            "live_check": False,
            "source": "unavailable",
            "repository": repository,
            "error": error,
            "summary": "Not checked.",
            "items": [],
        }

    def _get(self, url: str, token: str) -> list:
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {token}",
                "User-Agent": "OsirisAutomation",
            },
        )
        with urllib.request.urlopen(request, timeout=20) as response:
            body = response.read(1_000_000)
        parsed = json.loads(body.decode("utf-8"))
        if not isinstance(parsed, list):
            raise RuntimeError("GitHub returned an unexpected payload.")
        return parsed


def status_text(mode: str) -> str:
    if mode == "mock":
        return "Simulated. GitHub is not contacted."
    if mode == "dry_run":
        return "Dry run. Requests are not sent."
    if os.environ.get("GITHUB_TOKEN", "").strip():
        return "Live mode. A token is present in the environment and is not shown here."
    return "Live mode selected, but GITHUB_TOKEN is not set."
