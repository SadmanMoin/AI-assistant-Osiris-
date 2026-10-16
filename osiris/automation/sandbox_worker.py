"""Restricted script worker.

This process is started by Osiris. It reads one JSON payload on stdin and
writes one JSON result on stdout. Scripts cannot import modules. Tools are
the only way to touch data, and those tools use the snapshot the parent
supplied. This process does not receive API tokens.
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

from osiris.automation.validator import validate_source
from osiris.integrations.content import render_social_posts, suggest_reply_for_message
from osiris.security.paths import is_within
from osiris.util import utc_iso

_MAX_FILE_CHARS = 100_000
_DEFAULT_STEPS = 200_000


class _Bag:
    pass


class _Recorder:
    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.email_drafts: list[dict] = []
        self.social_drafts: list[dict] = []
        self.action_requests: list[dict] = []
        self.logs: list[str] = []

    def record(self, tool: str, result):
        self.calls.append({"tool": tool, "result": copy.deepcopy(result)})
        return copy.deepcopy(result)


def execute_payload(payload: dict) -> dict:
    source = payload.get("source", "")
    permissions = list(payload.get("permissions") or [])
    params = payload.get("params") or {}
    if not isinstance(params, dict):
        return _failure("Parameters must be an object.")
    validation = validate_source(source, permissions, max_chars=int(payload.get("max_script_chars") or 30000))
    if not validation["ok"]:
        return _failure("Validation failed. " + " ".join(validation["errors"]))

    recorder = _Recorder()
    tools = _build_tools(payload, permissions, recorder)
    stdout: list[str] = []
    limit = int(payload.get("max_output_chars") or 20000)
    steps = {"count": 0}
    budget = payload.get("instruction_budget", _DEFAULT_STEPS)

    def _print(*args, **kwargs):
        line = " ".join(str(arg) for arg in args)
        stdout.append(line)
        if sum(len(item) for item in stdout) > limit:
            raise RuntimeError("Printed output is too large.")

    safe_builtins = {
        "len": len,
        "range": range,
        "str": str,
        "int": int,
        "float": float,
        "bool": bool,
        "list": list,
        "dict": dict,
        "tuple": tuple,
        "set": set,
        "enumerate": enumerate,
        "zip": zip,
        "min": min,
        "max": max,
        "sum": sum,
        "sorted": sorted,
        "abs": abs,
        "round": round,
        "isinstance": isinstance,
        "repr": repr,
        "print": _print,
        "reversed": reversed,
        "any": any,
        "all": all,
        "True": True,
        "False": False,
        "None": None,
        "Exception": Exception,
        "ValueError": ValueError,
        "TypeError": TypeError,
        "KeyError": KeyError,
        "RuntimeError": RuntimeError,
    }
    sandbox = {"__builtins__": safe_builtins}
    tree_globals = sandbox

    def _trace(frame, event, arg):
        if event == "line":
            steps["count"] += 1
            if steps["count"] > budget:
                raise RuntimeError("The script exceeded the instruction budget.")
        return _trace

    previous = sys.gettrace()
    if budget:
        sys.settrace(_trace)
    try:
        exec(compile(source, "<automation>", "exec"), tree_globals, tree_globals)  # noqa: S102
        run = tree_globals.get("run")
        if not callable(run):
            return _failure("The script did not define run.")
        returned = run(tools, params)
    except Exception as error:
        return _failure(str(error), stdout=stdout, recorder=recorder)
    finally:
        sys.settrace(previous)

    if returned is None:
        returned = {"summary": "The script finished without a summary."}
    if not isinstance(returned, dict):
        returned = {"summary": str(returned)[:limit]}
    result = {
        "ok": True,
        "error": None,
        "returned": returned,
        "stdout": "\n".join(stdout)[:limit],
        "tool_calls": recorder.calls,
        "email_drafts": recorder.email_drafts,
        "social_drafts": recorder.social_drafts,
        "action_requests": recorder.action_requests,
        "logs": recorder.logs,
        "truncated": False,
    }
    encoded = json.dumps(result, default=str)
    if len(encoded) > limit:
        result["returned"] = {"summary": "The result was truncated because it was too large."}
        result["tool_calls"] = recorder.calls[:20]
        result["stdout"] = result["stdout"][:1000]
        result["truncated"] = True
    return result


def _failure(message: str, stdout=None, recorder: _Recorder | None = None) -> dict:
    recorder = recorder or _Recorder()
    return {
        "ok": False,
        "error": message[:4000],
        "returned": {},
        "stdout": "\n".join(stdout or [])[:4000],
        "tool_calls": recorder.calls,
        "email_drafts": recorder.email_drafts,
        "social_drafts": recorder.social_drafts,
        "action_requests": recorder.action_requests,
        "logs": recorder.logs,
        "truncated": False,
    }


def _build_tools(payload: dict, permissions: list[str], recorder: _Recorder):
    snapshot = payload.get("snapshot") or {}
    allowed = set(permissions)
    tools = _Bag()
    guidelines = str(payload.get("brand_guidelines") or "")
    preferences = str(payload.get("content_preferences") or "")

    if "projects.read" in allowed:
        projects = _Bag()

        def list_projects():
            return recorder.record("projects.list", list(snapshot.get("project_list") or []))

        def check_all():
            return recorder.record("projects.check_all", list(snapshot.get("projects") or []))

        projects.list = list_projects
        projects.check_all = check_all
        tools.projects = projects

    if "github.read" in allowed:
        github = _Bag()

        def list_recent_commits(repository="", limit=5):
            found = _github_part(snapshot, repository, "commits")
            return recorder.record("github.list_recent_commits", found)

        def list_issues(repository="", limit=5):
            found = _github_part(snapshot, repository, "issues")
            return recorder.record("github.list_issues", found)

        github.list_recent_commits = list_recent_commits
        github.list_issues = list_issues
        tools.github = github

    if any(name in allowed for name in ("email.read", "email.draft", "email.send")):
        email = _Bag()
        if "email.read" in allowed:
            def list_messages(limit=5):
                listing = snapshot.get("emails") or {
                    "ok": False,
                    "live": False,
                    "messages": [],
                    "notice": "Email data was not available to the runner.",
                }
                return recorder.record("email.list_messages", listing)

            email.list_messages = list_messages
        if "email.draft" in allowed:
            def suggest_reply(message_id=""):
                listing = snapshot.get("emails") or {}
                for message in listing.get("messages") or []:
                    if message.get("id") == message_id:
                        return recorder.record("email.suggest_reply", suggest_reply_for_message(message))
                return recorder.record(
                    "email.suggest_reply",
                    {"to": "", "subject": "", "body": "", "error": "Message was not in the mailbox snapshot.", "sent": False},
                )

            def save_draft(to="", subject="", body="", in_reply_to=""):
                draft = {
                    "to": str(to),
                    "subject": str(subject)[:300],
                    "body": str(body)[:20000],
                    "in_reply_to": str(in_reply_to),
                    "status": "draft",
                    "sent": False,
                }
                recorder.email_drafts.append(draft)
                return recorder.record("email.save_draft", draft)

            email.suggest_reply = suggest_reply
            email.save_draft = save_draft
        if "email.send" in allowed:
            def request_send(to="", subject="", body="", in_reply_to=""):
                request = {
                    "kind": "email.send",
                    "to": str(to),
                    "subject": str(subject)[:300],
                    "body": str(body)[:20000],
                    "in_reply_to": str(in_reply_to),
                    "sent": False,
                }
                recorder.action_requests.append(request)
                return recorder.record(
                    "email.request_send",
                    {"requested": True, "sent": False, "to": request["to"]},
                )

            email.request_send = request_send
        tools.email = email

    if any(name in allowed for name in ("social.draft", "social.publish")):
        social = _Bag()
        if "social.draft" in allowed:
            def create_draft(platform="linkedin", title="", body="", hashtags=None):
                text = str(body or "")
                tags = list(hashtags or [])
                heading = str(title or "a project update")
                if not text:
                    posts = render_social_posts(heading, guidelines=guidelines, preferences=preferences)
                    chosen = next((item for item in posts if item["platform"] == platform), posts[0])
                    text = chosen["body"]
                    tags = chosen["hashtags"]
                    heading = chosen["title"]
                draft = {
                    "platform": str(platform),
                    "title": heading[:120],
                    "body": text[:5000],
                    "hashtags": tags,
                    "status": "draft",
                    "published": False,
                    "provider_post_id": None,
                }
                recorder.social_drafts.append(draft)
                return recorder.record("social.create_draft", draft)

            social.create_draft = create_draft
        if "social.publish" in allowed:
            def request_publish(draft_id=""):
                request = {"kind": "social.publish", "draft_id": str(draft_id), "published": False}
                recorder.action_requests.append(request)
                return recorder.record(
                    "social.request_publish",
                    {"requested": True, "published": False, "draft_id": str(draft_id)},
                )

            social.request_publish = request_publish
        tools.social = social

    if "files.read" in allowed:
        files = _Bag()
        roots = [Path(item) for item in (payload.get("allowed_roots") or [])]

        def read_text(path):
            candidate = Path(str(path)).expanduser()
            allowed_root = any(root.is_dir() and is_within(candidate, root) for root in roots)
            if not allowed_root:
                result = {"ok": False, "error": "Path is outside the configured directories.", "text": ""}
                return recorder.record("files.read_text", result)
            if not candidate.is_file():
                result = {"ok": False, "error": "The path is not a file.", "text": ""}
                return recorder.record("files.read_text", result)
            text = candidate.read_text(encoding="utf-8", errors="replace")[:_MAX_FILE_CHARS]
            return recorder.record("files.read_text", {"ok": True, "error": None, "text": text})

        files.read_text = read_text
        tools.files = files

    log = _Bag()

    def info(message=""):
        recorder.logs.append(str(message)[:1000])
        return recorder.record("log.info", {"ok": True})

    log.info = info
    tools.log = log

    clock = _Bag()

    def now():
        return recorder.record("time.now", {"utc": utc_iso()})

    clock.now = now
    tools.time = clock
    return tools


def _github_part(snapshot: dict, repository, kind: str) -> dict:
    repo = str(repository or "").strip()
    entry = (snapshot.get("github") or {}).get(repo)
    if not isinstance(entry, dict) or kind not in entry:
        return {
            "ok": False,
            "live_check": False,
            "repository": repo,
            "source": "unavailable",
            "error": "Repository data was not available to the runner.",
            "summary": "Not checked.",
            "items": [],
        }
    result = copy.deepcopy(entry[kind])
    result.setdefault("repository", repo)
    return result


def main() -> int:
    raw = sys.stdin.buffer.read()
    try:
        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("Payload must be an object.")
        result = execute_payload(payload)
    except Exception as error:
        result = _failure(str(error))
    sys.stdout.buffer.write(json.dumps(result, default=str).encode("utf-8"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
