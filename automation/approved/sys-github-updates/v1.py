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
