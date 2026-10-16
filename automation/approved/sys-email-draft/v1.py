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
