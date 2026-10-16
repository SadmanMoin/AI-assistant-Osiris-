"""Social drafts. Publishing is a separate confirmed step and is off in mock mode."""

from __future__ import annotations

import hashlib
from typing import Callable

from osiris.audit.redact import redact_text
from osiris.db.database import Database
from osiris.errors import PolicyError
from osiris.integrations.content import render_social_posts
from osiris.security.policy import authorize_publish
from osiris.util import new_id, utc_iso

LivePublisher = Callable[[dict], dict]


class SocialService:
    def __init__(self, db: Database, mode: str = "mock", live_publisher: LivePublisher | None = None) -> None:
        self.db = db
        self.mode = mode if mode in {"mock", "dry_run", "live"} else "mock"
        self.live_publisher = live_publisher

    def create_drafts(
        self,
        topic: str,
        *,
        guidelines: str = "",
        preferences: str = "",
        language: str | None = None,
        execution_id: str = "",
    ) -> list[dict]:
        posts = render_social_posts(
            topic,
            guidelines=guidelines,
            preferences=preferences,
            language=language,
        )
        saved = []
        for post in posts:
            record = {
                "id": new_id(),
                "execution_id": execution_id or None,
                "platform": post["platform"],
                "title": post["title"],
                "body": post["body"],
                "hashtags": post["hashtags"],
                "status": "draft",
                "scheduled_for": None,
                "published_at": None,
                "provider_post_id": None,
                "created_at": utc_iso(),
            }
            self.db.save_social_draft(record)
            saved.append({**record, "published": False, "variations": post["variations"]})
        self.db.add_audit("social", "create_draft", topic[:80], "saved", "Drafts stored. Nothing was published.")
        return saved

    def approve_draft(self, draft_id: str) -> dict:
        draft = self._require(draft_id)
        self.db.update_social_draft(draft_id, status="approved")
        self.db.add_audit("social", "approve_draft", draft_id, "approved", draft["platform"])
        draft["status"] = "approved"
        return draft

    def publish(self, draft_id: str, *, confirmed: bool) -> dict:
        draft = self._require(draft_id)
        try:
            authorize_publish(confirmed=confirmed, draft_status=draft["status"])
        except PolicyError as error:
            self.db.add_audit("social", "publish", draft_id, "refused", str(error))
            return {
                "published": False,
                "status": "refused",
                "detail": str(error),
                "provider_post_id": None,
            }
        key = hashlib.sha256(
            f"{draft['platform']}|{draft['body']}".encode("utf-8")
        ).hexdigest()
        if not self.db.reserve_delivery(key, "social", draft["platform"]):
            self.db.add_audit("social", "publish", draft_id, "duplicate_prevented", "Duplicate publish blocked.")
            return {
                "published": False,
                "status": "duplicate_prevented",
                "detail": "This post was already submitted.",
                "provider_post_id": None,
            }
        if self.mode != "live" or self.live_publisher is None:
            reason = (
                "Mock and dry-run modes do not publish."
                if self.mode != "live"
                else "No authorized social integration is connected."
            )
            status = "not_published" if self.mode != "live" else "not_configured"
            self.db.finish_delivery(key, status, reason)
            self.db.add_audit("social", "publish", draft_id, status, reason)
            return {"published": False, "status": status, "detail": reason, "provider_post_id": None}
        try:
            result = self.live_publisher(draft)
        except Exception as error:
            detail = redact_text(str(error))
            self.db.finish_delivery(key, "failed", detail)
            self.db.add_audit("social", "publish", draft_id, "failed", detail)
            return {"published": False, "status": "failed", "detail": detail, "provider_post_id": None}
        provider_id = str(result.get("id", "")) if isinstance(result, dict) else ""
        if not provider_id:
            self.db.finish_delivery(key, "failed", "The publisher did not return a post id.")
            return {
                "published": False,
                "status": "failed",
                "detail": "The publisher did not return a post id, so this is not marked published.",
                "provider_post_id": None,
            }
        self.db.finish_delivery(key, "published", provider_id)
        self.db.update_social_draft(
            draft_id,
            status="published",
            published_at=utc_iso(),
            provider_post_id=provider_id,
        )
        self.db.add_audit("social", "publish", draft_id, "published", provider_id)
        return {"published": True, "status": "published", "detail": "Published.", "provider_post_id": provider_id}

    def _require(self, draft_id: str) -> dict:
        draft = self.db.get_social_draft(draft_id)
        if draft is None:
            raise PolicyError("That draft does not exist.")
        return draft
