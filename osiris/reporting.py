"""User-facing summaries built from tool results, not from script claims."""

from __future__ import annotations


def summarize_checks(results: list[dict]) -> str:
    if not results:
        return "No projects are configured, so nothing was checked."
    lines = []
    for result in results:
        name = result.get("name", "Project")
        if result.get("live_check"):
            lines.append(f"{name}: checked. {result.get('summary', '')}".rstrip())
        else:
            reason = result.get("error") or result.get("summary") or "The integration did not complete a live check."
            lines.append(f"{name}: not checked. {reason}")
    return "\n".join(lines)


def summarize_tool_calls(tool_calls: list[dict], script_summary: str) -> str:
    """Prefer observed tool results over text the script returns."""
    lines = []
    for call in tool_calls:
        name = call.get("tool", "")
        payload = call.get("result")
        if name == "projects.check_all" and isinstance(payload, list):
            lines.append(summarize_checks(payload))
        elif name in {"github.list_recent_commits", "github.list_issues"} and isinstance(payload, dict):
            repository = payload.get("repository") or "Repository"
            if payload.get("live_check"):
                lines.append(f"{repository}: checked. {payload.get('summary', '')}".rstrip())
            else:
                reason = payload.get("error") or payload.get("summary") or "GitHub was not checked."
                lines.append(f"{repository}: not checked. {reason}")
        elif name == "email.list_messages" and isinstance(payload, dict):
            if payload.get("live"):
                lines.append(payload.get("summary") or "Messages were read from the connected account.")
            else:
                lines.append(payload.get("notice") or payload.get("error") or "Email was not read from a connected account.")
        elif name == "email.save_draft":
            lines.append("A draft was saved on this computer. No email was sent.")
        elif name == "email.request_send":
            lines.append("A send was requested and is waiting for confirmation. No email was sent.")
        elif name == "social.create_draft":
            lines.append("A social draft was saved. Nothing was published.")
        elif name == "social.request_publish":
            lines.append("Publishing was requested and is waiting for approval. Nothing was published.")
        elif name == "files.read_text" and isinstance(payload, dict) and not payload.get("ok", True):
            lines.append(f"File read refused. {payload.get('error', '')}".rstrip())
    if not lines:
        return script_summary or "The automation finished."
    # Keep a script summary only when it does not contradict the observations.
    unique = []
    for line in lines:
        if line and line not in unique:
            unique.append(line)
    return "\n".join(unique)
