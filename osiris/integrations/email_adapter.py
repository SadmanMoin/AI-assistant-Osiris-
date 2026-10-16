"""Email adapter.

Live delivery is refused until an authorized sender is connected. Mock and
dry-run modes never deliver mail. Account passwords are not stored.
"""

from __future__ import annotations

import copy
import hashlib
from typing import Callable

from osiris.audit.redact import redact_text
from osiris.db.database import Database
from osiris.errors import PolicyError
from osiris.integrations.content import (
    SAMPLE_MESSAGES,
    categorize_message,
    suggest_reply_for_message,
    summarize_messages,
)
from osiris.security.policy import authorize_delivery
from osiris.util import clamp_int, new_id, utc_iso

LiveSender = Callable[[dict], dict]


class EmailService:
    def __init__(self, db: Database, mode: str = "mock", live_sender: LiveSender | None = None) -> None:
        self.db = db
        self.mode = mode if mode in {"mock", "dry_run", "live"} else "mock"
        self.live_sender = live_sender
        self.known_recipients: set[str] = set()

    def list_messages(self, limit: int = 5) -> dict:
        safe_limit = clamp_int(limit, 5, 1, 50)
        if self.mode == "live":
            return {
                "ok": False,
                "live": False,
                "source": "unavailable",
                "error": "No authorized email account is connected.",
                "notice": "Email was not read because no account is connected.",
                "messages": [],
            }
        messages = []
        for raw in SAMPLE_MESSAGES[:safe_limit]:
            item = copy.deepcopy(raw)
            item["priority"] = categorize_message(item)
            item["simulated"] = True
            item["untrusted"] = True
            messages.append(item)
            if item.get("from_address"):
                self.known_recipients.add(item["from_address"])
        notice = (
            "Dry run: no account was contacted."
            if self.mode == "dry_run"
            else "Simulated inbox. No account was contacted."
        )
        return {
            "ok": True,
            "live": False,
            "source": self.mode,
            "error": None,
            "notice": notice,
            "summary": summarize_messages(messages),
            "messages": messages,
        }

    def suggest_reply(self, message_id: str, language: str | None = None) -> dict:
        listing = self.list_messages(limit=20)
        for message in listing["messages"]:
            if message["id"] == message_id:
                draft = suggest_reply_for_message(message, language)
                draft["sent"] = False
                return draft
        return {
            "to": "",
            "subject": "",
            "body": "",
            "in_reply_to": message_id,
            "sent": False,
            "error": "That message is not in the current mailbox result.",
        }

    def save_draft(self, *, to: str, subject: str, body: str, in_reply_to: str = "", execution_id: str = "") -> dict:
        record = {
            "id": new_id(),
            "execution_id": execution_id or None,
            "message_id": in_reply_to,
            "recipient": to,
            "subject": subject,
            "body": body,
            "status": "draft",
            "created_at": utc_iso(),
        }
        self.db.save_email_draft(record)
        self.db.add_audit("email", "save_draft", to, "saved", "Draft stored locally. Nothing was sent.")
        return {
            "id": record["id"],
            "to": to,
            "subject": subject,
            "body": body,
            "in_reply_to": in_reply_to,
            "status": "draft",
            "sent": False,
        }

    def send(
        self,
        *,
        to: str,
        subject: str,
        body: str,
        in_reply_to: str = "",
        confirmed: bool = False,
        confirmed_to: str = "",
        known_recipients: set[str] | None = None,
    ) -> dict:
        allowed = set(self.known_recipients)
        if known_recipients:
            allowed.update(known_recipients)
        try:
            authorize_delivery(
                requested_to=to,
                confirmed_to=confirmed_to,
                known_recipients=allowed,
                confirmed=confirmed,
            )
        except PolicyError as error:
            self.db.add_audit("email", "send", to, "refused", redact_text(str(error)))
            return {"delivered": False, "status": "refused", "detail": str(error), "provider_id": None}

        key = hashlib.sha256(f"{to}|{subject}|{body}|{in_reply_to}".encode("utf-8")).hexdigest()
        if not self.db.reserve_delivery(key, "email", to):
            self.db.add_audit("email", "send", to, "duplicate_prevented", "Duplicate send blocked.")
            return {
                "delivered": False,
                "status": "duplicate_prevented",
                "detail": "This message was already submitted.",
                "provider_id": None,
            }

        if self.mode != "live" or self.live_sender is None:
            reason = (
                "Mock and dry-run modes do not deliver email."
                if self.mode != "live"
                else "No authorized email integration is connected."
            )
            status = "not_sent" if self.mode != "live" else "not_configured"
            self.db.finish_delivery(key, status, reason)
            self.db.add_audit("email", "send", to, status, reason)
            return {"delivered": False, "status": status, "mode": self.mode, "detail": reason, "provider_id": None}

        try:
            result = self.live_sender(
                {"to": to, "subject": subject, "body": body, "in_reply_to": in_reply_to}
            )
        except Exception as error:
            detail = redact_text(str(error), [])
            self.db.finish_delivery(key, "failed", detail)
            self.db.add_audit("email", "send", to, "failed", detail)
            return {"delivered": False, "status": "failed", "detail": detail, "provider_id": None}

        provider_id = str(result.get("id", "")) if isinstance(result, dict) else ""
        self.db.finish_delivery(key, "sent", provider_id)
        self.db.add_audit("email", "send", to, "sent", "Accepted by the connected sender.")
        return {"delivered": True, "status": "sent", "mode": "live", "detail": "Sent.", "provider_id": provider_id}
