"""Confirmation rules for external side effects.

External content never counts as approval. A send or publish proceeds only
when the person confirmed the exact target in the application.
"""

from __future__ import annotations

from osiris.errors import PolicyError


def authorize_delivery(
    *,
    requested_to: str,
    confirmed_to: str,
    known_recipients: set[str],
    confirmed: bool,
) -> None:
    recipient = (requested_to or "").strip()
    confirmed_recipient = (confirmed_to or "").strip()
    if not confirmed:
        raise PolicyError("Sending requires explicit confirmation.")
    if not recipient or not confirmed_recipient:
        raise PolicyError("A recipient must be shown and confirmed before sending.")
    if recipient != confirmed_recipient:
        raise PolicyError("Recipient does not match the confirmed recipient.")
    allowed = {item.strip() for item in known_recipients if item and item.strip()}
    if recipient not in allowed:
        raise PolicyError("Refusing an unverified or inferred recipient.")


def authorize_publish(*, confirmed: bool, draft_status: str) -> None:
    if draft_status not in {"approved", "scheduled"}:
        raise PolicyError("Only an approved draft can be published.")
    if not confirmed:
        raise PolicyError("Publishing requires explicit approval.")
