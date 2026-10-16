# Osiris AI Assistant

Osiris is the local desktop assistant in this folder. It still chats through LM Studio, and it can also plan a task, reuse or draft an automation, and run that automation only after you approve it.

The model does not get your files, email, or accounts by itself. It can only ask for tools Osiris already allows.

## Run

LM Studio should be open with Qwen 2.5 loaded and the local server on `http://localhost:1234/v1`.

```powershell
.\.venv\Scripts\python.exe gui.py
```

Leave the model name blank in Settings to use the first model LM Studio reports, which is what the original chat did. You can also set a model there, or with environment variables:

- `OSIRIS_LM_BASE_URL`
- `OSIRIS_LM_MODEL`
- `OSIRIS_LM_API_KEY` (optional; LM Studio accepts its local placeholder when this is unset)
- `OSIRIS_TIMEZONE` (for example `Asia/Dhaka`)
- `GITHUB_TOKEN` (only if you switch GitHub to live mode)

The key is read from the environment. It is not written into `data/settings.json` or into a script.

## What you can ask

Ordinary questions still go to Qwen as chat. These kinds of requests become a plan you can run:

- Check all my projects and summarize today's updates.
- Create a professional LinkedIn post about my latest project.
- Prepare a reply to the latest email from my client.
- Run my daily project monitoring automation.
- Show me all failed automation tasks.
- Create a reusable script for checking GitHub repository updates.
- Run my saved email drafting workflow.

Bangla works for the same kinds of requests. A proposed script is saved for review and stays disabled until its safe test passes and you approve it.

## Library layout

```text
automation/
  approved/     approved versions, one file per version
  pending/      drafts waiting for review
  tests/        safe-test reports
  archived/     copy kept when you archive a workflow
  manifests/    readable metadata for each workflow
data/
  osiris.db     automations, schedules, drafts, audit log
  logs/         redacted execution logs
  settings.json connection and timezone settings
```

Editing a script saves a new version. The approved file is not replaced in place. Roll back by choosing an older approved version.

## Integrations

Email, GitHub, social publishing, and URL checks start in mock mode. Mock and dry-run do not contact the account and are labeled as not checked or not sent.

- GitHub live mode uses `GITHUB_TOKEN` and the public GitHub API.
- A local Git project is a real check when `git` can read that folder.
- Email and social live delivery are refused until an authorized sender is connected. There is no password field.
- Sending mail or publishing a post shows the recipient or the post body and waits for a confirmation. A script cannot skip that step.

## Schedules

Daily, weekly, and interval schedules are stored in SQLite. They survive a restart. While Osiris is open it runs due work, including a catch-up when you start the app. It does not run in the background after you close the window. A second copy of the same automation will not start while one run is still going.

Stop all, in the sidebar, blocks new runs until you resume them in Settings.

## Tests

```powershell
$env:PYTHONIOENCODING = "utf-8"
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Tests do not send email, publish posts, or call GitHub. External integrations stay in mock or dry-run mode.

## Limits

The script runner is a separate process with a restricted Python builtin set, an allowlist of tools, a time limit, an output limit, and a Windows job that caps memory and process count. That is not a perfect operating-system sandbox. Scripts still cannot import modules, call a shell, or change their own permissions. Credentials are not placed in the worker environment.

A project line says "checked" only when that project's own read succeeded. A draft is not described as sent or published.
