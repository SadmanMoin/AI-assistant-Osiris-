"""Run approved scripts in a short-lived process with a clean environment."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

from osiris.audit.redact import redact_text, redact_value
from osiris.automation.validator import validate_source
from osiris.errors import ExecutionError
from osiris.reporting import summarize_tool_calls
from osiris.security.limits import close_job, limit_process, terminate_job
from osiris.util import new_id, sha256_text, utc_iso

_KEEP_ENV = (
    "PATH",
    "SYSTEMROOT",
    "WINDIR",
    "PATHEXT",
    "COMSPEC",
    "TEMP",
    "TMP",
    "USERPROFILE",
    "HOMEDRIVE",
    "HOMEPATH",
    "LOCALAPPDATA",
    "APPDATA",
    "PROGRAMFILES",
    "PROGRAMFILES(X86)",
    "SYSTEMDRIVE",
    "PROGRAMDATA",
)


def clean_environment(project_root: Path) -> dict[str, str]:
    """Environment for the worker. Parent secrets are not copied."""
    env = {key: os.environ[key] for key in _KEEP_ENV if key in os.environ}
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    env["PYTHONNOUSERSITE"] = "1"
    env["PYTHONPATH"] = str(project_root)
    env["OSIRIS_SANDBOX"] = "1"
    return env


class Runner:
    def __init__(self, *, project_root: Path, settings, db, snapshot_builder, logs_dir: Path) -> None:
        self.project_root = Path(project_root)
        self.settings = settings
        self.db = db
        self.snapshot_builder = snapshot_builder
        self.logs_dir = Path(logs_dir)
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._active: dict[int, tuple[subprocess.Popen, int]] = {}

    def stop_active(self) -> int:
        with self._lock:
            active = list(self._active.values())
        for process, job_handle in active:
            terminate_job(job_handle)
            process.kill()
        return len(active)

    def run(
        self,
        *,
        source: str,
        permissions: list[str],
        params: dict | None = None,
        test_mode: bool = False,
        automation_id: str = "",
        name: str = "",
        trigger: str = "manual",
        version: int | None = None,
        instruction_budget: int | None = 200_000,
        timeout_seconds: int | None = None,
    ) -> dict:
        if self.db.get_flag("emergency_stop") == "1":
            raise ExecutionError("Automations are stopped.")
        params = dict(params or {})
        if len(json.dumps(params)) > 100_000:
            raise ExecutionError("Parameters are too large.")
        validation = validate_source(source, permissions, max_chars=self.settings.max_script_chars)
        if not validation["ok"]:
            return self._rejected(validation["errors"])

        execution_id = new_id()
        if automation_id:
            begun = self.db.begin_execution(
                {
                    "id": execution_id,
                    "automation_id": automation_id,
                    "name": name,
                    "version": version,
                    "trigger": trigger,
                    "started_at": utc_iso(),
                }
            )
            if begun is None:
                raise ExecutionError("This automation is already running.")
        else:
            self.db.begin_execution(
                {
                    "id": execution_id,
                    "automation_id": "",
                    "name": name or "Direct action",
                    "version": version,
                    "trigger": trigger,
                    "started_at": utc_iso(),
                }
            )

        try:
            if self.db.get_flag("emergency_stop") == "1":
                raise ExecutionError("Automations are stopped.")
            snapshot = self.snapshot_builder(permissions, params, test_mode)
            payload = {
                "source": source,
                "permissions": permissions,
                "params": params,
                "snapshot": snapshot,
                "allowed_roots": list(self.settings.allowed_directories),
                "max_output_chars": self.settings.max_output_chars,
                "max_script_chars": self.settings.max_script_chars,
                "instruction_budget": instruction_budget,
                "brand_guidelines": self.settings.brand_guidelines,
                "content_preferences": self.settings.content_preferences,
            }
            worker = self._spawn(payload, timeout_seconds or self.settings.execution_timeout_seconds)
        except Exception as error:
            summary = "The automation did not finish."
            detail = redact_text(str(error), self.settings.secret_values())
            self._finish(execution_id, "failed", summary, detail, "", [])
            if isinstance(error, ExecutionError):
                raise
            raise ExecutionError(detail) from error

        secrets = self.settings.secret_values()
        script_summary = ""
        returned = worker.get("returned") if isinstance(worker.get("returned"), dict) else {}
        if isinstance(returned, dict):
            script_summary = str(returned.get("summary") or "")
        summary = summarize_tool_calls(worker.get("tool_calls") or [], script_summary)
        summary = redact_text(summary, secrets)
        error_text = redact_text(str(worker.get("error") or ""), secrets)
        status = "completed" if worker.get("ok") else "failed"
        if worker.get("timed_out"):
            status = "failed"
            error_text = error_text or "The script exceeded the time limit."
            summary = "The script was stopped because it exceeded the time limit."
        log_path = self._write_log(
            execution_id,
            {
                "summary": summary,
                "script_summary": redact_text(script_summary, secrets),
                "status": status,
                "error": error_text,
                "tool_calls": redact_value(worker.get("tool_calls") or [], secrets),
                "stdout": redact_text(str(worker.get("stdout") or ""), secrets),
                "stderr": redact_text(str(worker.get("stderr") or ""), secrets),
                "action_requests": worker.get("action_requests") or [],
                "test_mode": test_mode,
                "source_sha256": sha256_text(source),
                "limits": worker.get("limits"),
            },
        )
        self._finish(
            execution_id,
            status,
            summary,
            error_text,
            str(log_path),
            worker.get("action_requests") or [],
        )
        self.db.add_audit(
            "runner",
            "execute",
            automation_id or name,
            status,
            summary[:500],
        )
        return {
            "ok": status == "completed",
            "status": status,
            "summary": summary,
            "script_summary": redact_text(script_summary, secrets),
            "error": error_text or None,
            "execution_id": execution_id,
            "tool_calls": worker.get("tool_calls") or [],
            "action_requests": worker.get("action_requests") or [],
            "email_drafts": worker.get("email_drafts") or [],
            "social_drafts": worker.get("social_drafts") or [],
            "log_path": str(log_path),
            "truncated": bool(worker.get("truncated")),
            "test_mode": test_mode,
        }

    def _spawn(self, payload: dict, timeout_seconds: int) -> dict:
        env = clean_environment(self.project_root)
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        work_dir = Path(tempfile.mkdtemp(prefix="osiris-run-"))
        # sys.executable may be a venv launcher that spawns a second process.
        # The job's process cap has to fit that, so prefer the base interpreter.
        executable = getattr(sys, "_base_executable", None) or sys.executable
        process = subprocess.Popen(
            [executable, "-m", "osiris.automation.sandbox_worker"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            cwd=str(work_dir),
            creationflags=creationflags,
        )
        job = limit_process(
            process,
            memory_mb=self.settings.memory_limit_mb,
            cpu_seconds=max(timeout_seconds, 1),
        )
        with self._lock:
            self._active[process.pid] = (process, job.handle)
        timed_out = False
        try:
            stdout, stderr = process.communicate(
                input=json.dumps(payload).encode("utf-8"),
                timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired:
            timed_out = True
            terminate_job(job.handle)
            process.kill()
            stdout, stderr = process.communicate()
        finally:
            with self._lock:
                self._active.pop(process.pid, None)
            close_job(job.handle)
            try:
                work_dir.rmdir()
            except OSError:
                pass
        if timed_out:
            return {
                "ok": False,
                "timed_out": True,
                "error": "The script exceeded the time limit.",
                "stdout": "",
                "stderr": (stderr or b"").decode("utf-8", errors="replace")[:4000],
                "tool_calls": [],
                "action_requests": [],
                "email_drafts": [],
                "social_drafts": [],
                "limits": job.detail,
            }
        text = (stdout or b"").decode("utf-8", errors="replace")
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            parsed = {
                "ok": False,
                "error": "The worker did not return a result.",
                "stdout": text[:2000],
                "tool_calls": [],
                "action_requests": [],
                "email_drafts": [],
                "social_drafts": [],
            }
        if not isinstance(parsed, dict):
            parsed = {"ok": False, "error": "The worker returned an unexpected result."}
        parsed["stderr"] = (stderr or b"").decode("utf-8", errors="replace")[:4000]
        parsed["timed_out"] = False
        parsed["limits"] = job.detail
        return parsed

    def _rejected(self, errors: list[str]) -> dict:
        message = " ".join(errors)
        return {
            "ok": False,
            "status": "rejected",
            "summary": "The script failed validation and was not executed.",
            "script_summary": "",
            "error": message,
            "execution_id": None,
            "tool_calls": [],
            "action_requests": [],
            "email_drafts": [],
            "social_drafts": [],
            "log_path": "",
            "truncated": False,
            "test_mode": False,
        }

    def _finish(self, execution_id, status, summary, error, log_path, actions) -> None:
        self.db.finish_execution(
            execution_id,
            status=status,
            summary=summary,
            error=error,
            log_path=log_path,
            action_requests=actions,
        )

    def _write_log(self, execution_id: str, payload: dict) -> Path:
        path = self.logs_dir / f"{execution_id}.json"
        path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        return path
