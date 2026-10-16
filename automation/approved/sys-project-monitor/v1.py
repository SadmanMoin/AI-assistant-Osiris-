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
        summary = "\n".join(lines)
    return {"summary": summary, "projects": report}
