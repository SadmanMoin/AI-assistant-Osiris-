"""Shipped automations. They are reviewed source, still validated like any script."""

from __future__ import annotations

PROJECT_MONITOR = '''
def run(tools, params):
    report = tools.projects.check_all()
    lines = []
    for project in report:
        name = str(project.get("name", "Project"))
        if project.get("live_check"):
            lines.append(name + ": checked. " + str(project.get("summary", "")))
        else:
            reason = project.get("error") or project.get("summary") or "No live check was completed."
            lines.append(name + ": not checked. " + str(reason))
    if not report:
        summary = "No projects are configured, so nothing was checked."
    else:
        summary = "\\n".join(lines)
    return {"summary": summary, "projects": report}
'''.strip() + "\n"

EMAIL_DRAFT = '''
def run(tools, params):
    listing = tools.email.list_messages(limit=params.get("limit", 5))
    messages = listing.get("messages", [])
    if not messages:
        notice = listing.get("error") or listing.get("notice") or "No messages were returned."
        return {"summary": "No email was read from a connected account. " + str(notice), "draft": None}
    latest = messages[0]
    reply = tools.email.suggest_reply(latest.get("id", ""))
    draft = tools.email.save_draft(
        to=latest.get("from_address", ""),
        subject=reply.get("subject", ""),
        body=reply.get("body", ""),
        in_reply_to=latest.get("id", ""),
    )
    return {"summary": "A draft reply was prepared. No email was sent.", "draft": draft}
'''.strip() + "\n"

GITHUB_UPDATES = '''
def run(tools, params):
    repository = str(params.get("repository", "")).strip()
    if not repository:
        return {"summary": "No repository was provided, so GitHub was not checked."}
    commits = tools.github.list_recent_commits(repository=repository, limit=params.get("limit", 5))
    issues = tools.github.list_issues(repository=repository, limit=params.get("limit", 5))
    if commits.get("live_check") and issues.get("live_check"):
        summary = "Checked " + repository + ". " + str(commits.get("summary", ""))
    else:
        reason = commits.get("error") or commits.get("summary") or "GitHub was not checked."
        summary = repository + ": not checked. " + str(reason)
    return {"summary": summary, "commits": commits, "issues": issues}
'''.strip() + "\n"

SOCIAL_DRAFT = '''
def run(tools, params):
    topic = str(params.get("topic", "a project update"))
    draft = tools.social.create_draft(platform="linkedin", title=topic, body="", hashtags=[])
    return {"summary": "A social draft was saved. Nothing was published.", "draft": draft}
'''.strip() + "\n"

SYSTEM_AUTOMATIONS = [
    {
        "id": "sys-project-monitor",
        "name": "Daily project monitoring",
        "description": "Read configured projects and report what was actually checked.",
        "source": PROJECT_MONITOR,
        "permissions": ["projects.read"],
        "integrations": ["projects"],
        "parameters": {},
    },
    {
        "id": "sys-email-draft",
        "name": "Email drafting workflow",
        "description": "Prepare a reply to the latest available message. It does not send mail.",
        "source": EMAIL_DRAFT,
        "permissions": ["email.read", "email.draft"],
        "integrations": ["email"],
        "parameters": {"limit": {"type": "int", "required": False, "label": "How many messages to read", "default": 5}},
    },
    {
        "id": "sys-github-updates",
        "name": "GitHub repository updates",
        "description": "Read recent commits and open issues for one repository.",
        "source": GITHUB_UPDATES,
        "permissions": ["github.read"],
        "integrations": ["github"],
        "parameters": {
            "repository": {"type": "string", "required": True, "label": "Repository (owner/name)"},
            "limit": {"type": "int", "required": False, "label": "How many items", "default": 5},
        },
    },
    {
        "id": "sys-social-draft",
        "name": "Daily social draft",
        "description": "Save a LinkedIn draft from a topic. It does not publish.",
        "source": SOCIAL_DRAFT,
        "permissions": ["social.draft"],
        "integrations": ["social"],
        "parameters": {"topic": {"type": "string", "required": True, "label": "Topic"}},
    },
]
