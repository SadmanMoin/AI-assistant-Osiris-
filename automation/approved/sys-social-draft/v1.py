def run(tools, params):
    topic = str(params.get("topic", "a project update"))
    draft = tools.social.create_draft(platform="linkedin", title=topic, body="", hashtags=[])
    return {"summary": "A social draft was saved. Nothing was published.", "draft": draft}
