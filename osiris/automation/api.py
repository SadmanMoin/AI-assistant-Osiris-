"""Allowlisted tool API. Scripts may call only these names."""

from __future__ import annotations

# None means the tool is always available and has no side effect.
TOOL_PERMISSIONS = {
    "projects.list": "projects.read",
    "projects.check_all": "projects.read",
    "github.list_recent_commits": "github.read",
    "github.list_issues": "github.read",
    "email.list_messages": "email.read",
    "email.suggest_reply": "email.draft",
    "email.save_draft": "email.draft",
    "email.request_send": "email.send",
    "social.create_draft": "social.draft",
    "social.request_publish": "social.publish",
    "files.read_text": "files.read",
    "log.info": None,
    "time.now": None,
}

TOOL_SIGNATURES = {
    "projects.list": "tools.projects.list()",
    "projects.check_all": "tools.projects.check_all()",
    "github.list_recent_commits": "tools.github.list_recent_commits(repository, limit)",
    "github.list_issues": "tools.github.list_issues(repository, limit)",
    "email.list_messages": "tools.email.list_messages(limit)",
    "email.suggest_reply": "tools.email.suggest_reply(message_id)",
    "email.save_draft": "tools.email.save_draft(to, subject, body, in_reply_to)",
    "email.request_send": "tools.email.request_send(to, subject, body, in_reply_to)",
    "social.create_draft": "tools.social.create_draft(platform, title, body, hashtags)",
    "social.request_publish": "tools.social.request_publish(draft_id)",
    "files.read_text": "tools.files.read_text(path)",
    "log.info": "tools.log.info(message)",
    "time.now": "tools.time.now()",
}

PERMISSION_EFFECTS = {
    "projects.read": "Reads configured projects. A project is reported as checked only when that read succeeds.",
    "github.read": "Reads GitHub when live mode and GITHUB_TOKEN are set. Mock mode does not contact GitHub.",
    "email.read": "Reads messages. Mock mode uses a simulated inbox and does not contact an account.",
    "email.draft": "Saves a reply draft on this computer. It does not send email.",
    "email.send": "Can request a send. Osiris still shows the recipient, subject, and body for confirmation. Mock mode does not deliver mail.",
    "social.draft": "Saves a social draft on this computer. It does not publish.",
    "social.publish": "Can request publishing. Osiris still asks for approval. Mock mode does not publish.",
    "files.read": "Reads text files inside directories you listed in Settings.",
}

KNOWN_PERMISSIONS = sorted({value for value in TOOL_PERMISSIONS.values() if value})


def effects_for(permissions: list[str]) -> list[str]:
    return [PERMISSION_EFFECTS[name] for name in permissions if name in PERMISSION_EFFECTS]
