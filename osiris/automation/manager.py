"""Automation library. Approved files are never overwritten in place."""

from __future__ import annotations

import json
from pathlib import Path

from osiris.automation.builtins import SYSTEM_AUTOMATIONS
from osiris.automation.validator import validate_source
from osiris.errors import ApprovalError, ExecutionError
from osiris.util import new_id, sha256_text, utc_iso


class AutomationManager:
    def __init__(self, root: Path, db, runner) -> None:
        self.root = Path(root)
        self.db = db
        self.runner = runner
        for name in ("approved", "pending", "tests", "archived", "manifests"):
            (self.root / name).mkdir(parents=True, exist_ok=True)

    def ensure_system(self) -> None:
        for spec in SYSTEM_AUTOMATIONS:
            if self.db.get_automation(spec["id"]) is None:
                self.install_system(spec)

    def install_system(self, spec: dict) -> dict:
        validation = validate_source(spec["source"], spec["permissions"])
        if not validation["ok"]:
            raise ApprovalError("Shipped script failed validation: " + " ".join(validation["errors"]))
        now = utc_iso()
        path = self.root / "approved" / spec["id"] / "v1.py"
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.read_text(encoding="utf-8") != spec["source"]:
            raise ApprovalError("Refusing to overwrite a shipped script that was changed on disk.")
        if not path.exists():
            path.write_text(spec["source"], encoding="utf-8")
        self.db.insert_automation(
            {
                "id": spec["id"],
                "name": spec["name"],
                "description": spec["description"],
                "status": "approved",
                "enabled": True,
                "current_version": 1,
                "permissions": list(spec["permissions"]),
                "integrations": list(spec["integrations"]),
                "parameters": spec["parameters"],
                "created_at": now,
                "updated_at": now,
                "last_execution_at": None,
                "last_status": None,
            }
        )
        self.db.insert_version(
            {
                "automation_id": spec["id"],
                "version": 1,
                "script_path": str(path),
                "source_sha256": sha256_text(spec["source"]),
                "permissions": list(spec["permissions"]),
                "parameters": spec["parameters"],
                "validation": validation,
                "test_results": {
                    "ok": False,
                    "status": "not_run",
                    "summary": "Shipped script passed static validation. Run a safe test to record an execution.",
                },
                "status": "approved",
                "created_at": now,
                "approved_at": now,
            }
        )
        self._write_manifest(spec["id"])
        self.db.add_audit("library", "install_system", spec["id"], "approved", spec["name"])
        return self.db.get_automation(spec["id"])

    def create(
        self,
        *,
        name: str,
        description: str,
        source: str,
        permissions: list[str],
        integrations: list[str] | None = None,
        parameters: dict | None = None,
    ) -> dict:
        name = name.strip()
        if not name:
            raise ApprovalError("A script needs a name.")
        now = utc_iso()
        automation_id = new_id()
        self.db.insert_automation(
            {
                "id": automation_id,
                "name": name,
                "description": description.strip(),
                "status": "draft",
                "enabled": False,
                "current_version": 0,
                "permissions": permissions,
                "integrations": list(integrations or []),
                "parameters": parameters or {},
                "created_at": now,
                "updated_at": now,
                "last_execution_at": None,
                "last_status": None,
            }
        )
        version = self.add_version(
            automation_id,
            source=source,
            permissions=permissions,
            parameters=parameters or {},
        )
        return {"automation": self.db.get_automation(automation_id), "version": version}

    def add_version(self, automation_id: str, *, source: str, permissions: list[str], parameters: dict | None = None) -> dict:
        record = self._require(automation_id)
        if record["status"] == "archived":
            raise ApprovalError("Archived automations cannot take a new version.")
        version = self.db.latest_version_number(automation_id) + 1
        validation = validate_source(source, permissions, max_chars=self.runner.settings.max_script_chars)
        path = self.root / "pending" / automation_id / f"v{version}.py"
        if path.exists():
            raise ApprovalError("Refusing to overwrite an existing script file.")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
        stored_permissions = sorted(set(permissions) | set(validation.get("required_permissions") or []))
        now = utc_iso()
        self.db.insert_version(
            {
                "automation_id": automation_id,
                "version": version,
                "script_path": str(path),
                "source_sha256": sha256_text(source),
                "permissions": stored_permissions,
                "parameters": parameters if parameters is not None else record["parameters"],
                "validation": validation,
                "test_results": {"ok": False, "status": "not_run", "summary": ""},
                "status": "pending_approval" if validation["ok"] else "draft",
                "created_at": now,
                "approved_at": None,
            }
        )
        status = record["status"]
        if status not in {"approved", "disabled"}:
            status = "pending_approval" if validation["ok"] else "draft"
        self.db.update_automation(
            automation_id,
            status=status,
            permissions=stored_permissions if status != "approved" else record["permissions"],
            parameters=parameters if parameters is not None and status != "approved" else record["parameters"],
            updated_at=now,
        )
        if validation["ok"]:
            self.run_test(automation_id, version, params=_sample_params(parameters or record["parameters"]))
        self._write_manifest(automation_id)
        self.db.add_audit("library", "add_version", automation_id, "pending", f"v{version}")
        return self.db.get_version(automation_id, version)

    def run_test(self, automation_id: str, version: int, params: dict | None = None) -> dict:
        row = self._version(automation_id, version)
        if not row["validation"].get("ok"):
            result = {
                "ok": False,
                "status": "rejected",
                "summary": "Validation failed, so the safe test did not run.",
                "error": " ".join(row["validation"].get("errors") or []),
            }
            self.db.update_version(automation_id, version, test_results=result)
            return result
        source = self._read_verified(row)
        result = self.runner.run(
            source=source,
            permissions=row["permissions"],
            params=params or {},
            test_mode=True,
            automation_id=automation_id,
            name=self._require(automation_id)["name"],
            trigger="test",
            version=version,
        )
        stored = {
            "ok": result["ok"],
            "status": result["status"],
            "summary": result["summary"],
            "error": result.get("error"),
        }
        self.db.update_version(automation_id, version, test_results=stored)
        report = self.root / "tests" / automation_id / f"v{version}.json"
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(json.dumps(stored, indent=2), encoding="utf-8")
        return result

    def approve(self, automation_id: str, version: int) -> dict:
        if self.db.get_flag("emergency_stop") == "1":
            raise ApprovalError("Automations are stopped. Resume them before approving.")
        record = self._require(automation_id)
        row = self._version(automation_id, version)
        if row["status"] == "approved":
            return record
        if not row["validation"].get("ok"):
            raise ApprovalError("Fix the validation errors before approval.")
        if not (row["test_results"] or {}).get("ok"):
            raise ApprovalError("Run the safe test successfully before approval.")
        source = self._read_verified(row)
        target = self.root / "approved" / automation_id / f"v{version}.py"
        if target.exists():
            raise ApprovalError("That approved version file already exists and was not replaced.")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source, encoding="utf-8")
        now = utc_iso()
        self.db.update_version(
            automation_id,
            version,
            status="approved",
            script_path=str(target),
            source_sha256=sha256_text(source),
            approved_at=now,
        )
        self.db.update_automation(
            automation_id,
            status="approved",
            enabled=True,
            current_version=version,
            permissions=row["permissions"],
            parameters=row["parameters"],
            updated_at=now,
        )
        self._write_manifest(automation_id)
        self.db.add_audit("library", "approve", automation_id, "approved", f"v{version}")
        return self.db.get_automation(automation_id)

    def reject(self, automation_id: str, version: int) -> None:
        self._version(automation_id, version)
        self.db.update_version(automation_id, version, status="rejected")
        self.db.add_audit("library", "reject", automation_id, "rejected", f"v{version}")

    def rollback(self, automation_id: str, version: int) -> dict:
        row = self._version(automation_id, version)
        if row["status"] != "approved":
            raise ApprovalError("Only an approved version can be restored.")
        self._read_verified(row)
        now = utc_iso()
        self.db.update_automation(
            automation_id,
            current_version=version,
            status="approved",
            enabled=True,
            permissions=row["permissions"],
            parameters=row["parameters"],
            updated_at=now,
        )
        self._write_manifest(automation_id)
        self.db.add_audit("library", "rollback", automation_id, "restored", f"v{version}")
        return self.db.get_automation(automation_id)

    def set_enabled(self, automation_id: str, enabled: bool) -> dict:
        record = self._require(automation_id)
        if record["status"] == "archived":
            raise ExecutionError("Archived automations stay disabled.")
        if enabled and record["status"] not in {"approved", "disabled"}:
            raise ExecutionError("Approve the automation before enabling it.")
        if enabled and not record["current_version"]:
            raise ExecutionError("There is no approved version to enable.")
        status = "approved" if enabled else "disabled"
        self.db.update_automation(automation_id, enabled=enabled, status=status, updated_at=utc_iso())
        self._write_manifest(automation_id)
        self.db.add_audit("library", "set_enabled", automation_id, status, "")
        return self.db.get_automation(automation_id)

    def archive(self, automation_id: str) -> dict:
        record = self._require(automation_id)
        if record["current_version"]:
            row = self.db.get_version(automation_id, record["current_version"])
            if row is not None and Path(row["script_path"]).is_file():
                source = Path(row["script_path"]).read_text(encoding="utf-8")
                target = self.root / "archived" / automation_id / f"v{row['version']}.py"
                target.parent.mkdir(parents=True, exist_ok=True)
                if not target.exists():
                    target.write_text(source, encoding="utf-8")
        self.db.update_automation(automation_id, status="archived", enabled=False, updated_at=utc_iso())
        self.db.disable_schedules_for(automation_id)
        self._write_manifest(automation_id)
        self.db.add_audit("library", "archive", automation_id, "archived", record["name"])
        return self.db.get_automation(automation_id)

    def run(self, automation_id: str, params: dict | None = None, trigger: str = "manual") -> dict:
        record = self._require(automation_id)
        if record["status"] != "approved" or not record["enabled"]:
            raise ExecutionError("Only an enabled, approved automation can run.")
        row = self._version(automation_id, record["current_version"])
        if row["status"] != "approved":
            raise ExecutionError("The current version is not approved.")
        source = self._read_verified(row)
        checked = _normalize_params(params or {}, row["parameters"] or record["parameters"])
        result = self.runner.run(
            source=source,
            permissions=row["permissions"],
            params=checked,
            test_mode=False,
            automation_id=automation_id,
            name=record["name"],
            trigger=trigger,
            version=row["version"],
        )
        self._persist_outputs(result)
        self.db.update_automation(
            automation_id,
            last_execution_at=utc_iso(),
            last_status=result["status"],
            updated_at=utc_iso(),
        )
        self._write_manifest(automation_id)
        return result

    def read_source(self, automation_id: str, version: int) -> dict:
        row = self._version(automation_id, version)
        path = Path(row["script_path"])
        if not path.is_file():
            return {"source": "", "matches": False, "error": "The script file is missing."}
        source = path.read_text(encoding="utf-8")
        return {
            "source": source,
            "matches": sha256_text(source) == row["source_sha256"],
            "error": "" if sha256_text(source) == row["source_sha256"] else "The file does not match the stored version.",
        }

    def search(self, query: str) -> list[dict]:
        return self.db.list_automations(query)

    def _persist_outputs(self, result: dict) -> None:
        execution_id = result.get("execution_id")
        now = utc_iso()
        for draft in result.get("email_drafts") or []:
            self.db.save_email_draft(
                {
                    "id": new_id(),
                    "execution_id": execution_id,
                    "message_id": draft.get("in_reply_to"),
                    "recipient": draft.get("to", ""),
                    "subject": draft.get("subject", ""),
                    "body": draft.get("body", ""),
                    "status": "draft",
                    "created_at": now,
                }
            )
        for draft in result.get("social_drafts") or []:
            self.db.save_social_draft(
                {
                    "id": new_id(),
                    "execution_id": execution_id,
                    "platform": draft.get("platform", "linkedin"),
                    "title": draft.get("title", ""),
                    "body": draft.get("body", ""),
                    "hashtags": draft.get("hashtags") or [],
                    "status": "draft",
                    "scheduled_for": None,
                    "published_at": None,
                    "provider_post_id": None,
                    "created_at": now,
                }
            )

    def _write_manifest(self, automation_id: str) -> None:
        record = self.db.get_automation(automation_id)
        if record is None:
            return
        version = None
        if record["current_version"]:
            version = self.db.get_version(automation_id, record["current_version"])
        manifest = {
            "id": record["id"],
            "name": record["name"],
            "description": record["description"],
            "status": record["status"],
            "enabled": record["enabled"],
            "version": record["current_version"],
            "permissions": record["permissions"],
            "integrations": record["integrations"],
            "parameters": record["parameters"],
            "created_at": record["created_at"],
            "updated_at": record["updated_at"],
            "last_execution_at": record["last_execution_at"],
            "last_status": record["last_status"],
            "script_file": None if version is None else version["script_path"],
            "source_sha256": None if version is None else version["source_sha256"],
            "test_results": None if version is None else version["test_results"],
        }
        path = self.root / "manifests" / f"{automation_id}.json"
        path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    def _require(self, automation_id: str) -> dict:
        record = self.db.get_automation(automation_id)
        if record is None:
            raise ExecutionError("That automation does not exist.")
        return record

    def _version(self, automation_id: str, version: int) -> dict:
        row = self.db.get_version(automation_id, version)
        if row is None:
            raise ExecutionError("That version does not exist.")
        return row

    def _read_verified(self, version_row: dict) -> str:
        path = Path(version_row["script_path"])
        if not path.is_file():
            raise ExecutionError("The script file is missing.")
        source = path.read_text(encoding="utf-8")
        if sha256_text(source) != version_row["source_sha256"]:
            raise ExecutionError("The script file does not match the stored version, so it was not run.")
        return source


def _sample_params(schema: dict) -> dict:
    params = {}
    for name, spec in (schema or {}).items():
        if not isinstance(spec, dict):
            params[name] = spec
            continue
        if "default" in spec:
            params[name] = spec["default"]
        elif spec.get("type") == "int":
            params[name] = 1
        elif spec.get("required"):
            params[name] = "example/repository" if "repo" in name else "sample"
    return params


def _normalize_params(params: dict, schema: dict) -> dict:
    checked = dict(params)
    for name, spec in (schema or {}).items():
        if not isinstance(spec, dict):
            continue
        if spec.get("required") and not str(checked.get(name, "")).strip():
            raise ExecutionError(f"Missing required input: {spec.get('label') or name}.")
        if name in checked and spec.get("type") == "int":
            try:
                checked[name] = int(checked[name])
            except (TypeError, ValueError) as error:
                raise ExecutionError(f"{name} must be a whole number.") from error
    return checked
