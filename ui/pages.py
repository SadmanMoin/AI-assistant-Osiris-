"""Dashboard and management pages."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from osiris.reporting import summarize_checks
from osiris.scheduler.service import describe_next
from ui.common import Page, ask_parameters, button_row, card, confirm, fill_table, selected_id
from ui.theme import status_text


class DashboardPage(Page):
    def __init__(self, osiris, host):
        super().__init__(osiris)
        self.host = host
        self.title("Dashboard", "What Osiris has actually done on this computer.")
        self.cards = QGridLayout()
        self.layout.addLayout(self.cards)
        self.notice = QLabel("")
        self.notice.setWordWrap(True)
        self.layout.addWidget(self.notice)
        self.history = QTableWidget()
        self.upcoming = QTableWidget()
        columns = QHBoxLayout()
        left = QVBoxLayout()
        right = QVBoxLayout()
        left.addWidget(QLabel("Recent runs"))
        left.addWidget(self.history)
        right.addWidget(QLabel("Upcoming schedules"))
        right.addWidget(self.upcoming)
        columns.addLayout(left, 3)
        columns.addLayout(right, 2)
        holder = QWidget()
        holder.setLayout(columns)
        self.layout.addWidget(holder, 1)
        self.card_labels = []

    def refresh(self) -> None:
        data = self.osiris.dashboard()
        values = [
            ("Automations", str(data["automations"]), "Saved workflows, including disabled ones"),
            ("Completed today", str(data["completed_today"]), "Runs that finished since local midnight"),
            ("Running", str(data["running"]), "Runs that have not finished"),
            ("Failed today", str(data["failed_today"]), "Runs that stopped with an error"),
        ]
        while self.cards.count():
            item = self.cards.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for index, (label, value, note) in enumerate(values):
            self.cards.addWidget(card(label, value, note), 0, index)
        if self.osiris.emergency_stopped():
            self.notice.setText("Automations are stopped. Resume them in Settings before a schedule can run.")
            self.notice.setStyleSheet("color: #FF8585;")
        else:
            notes = data["notifications"]
            self.notice.setText(notes[0]["body"] if notes else "No new failure notifications.")
            self.notice.setStyleSheet("color: #A0A7B4;")
        fill_table(
            self.history,
            ["When", "Name", "Status", "Summary"],
            [
                [row["started_at"][:19], row.get("name") or "", row["status"], (row.get("summary") or "")[:120]]
                for row in data["recent"]
            ],
        )
        fill_table(
            self.upcoming,
            ["Name", "Next run", "Last status"],
            [[item["name"], describe_next(item), item.get("last_status") or "not run"] for item in data["upcoming"]],
        )


class AutomationsPage(Page):
    def __init__(self, osiris, host):
        super().__init__(osiris)
        self.host = host
        self.title("Automations", "Enable, run, or archive a saved workflow. Approved files are not overwritten.")
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search by name or description")
        self.search.textChanged.connect(self.refresh)
        self.layout.addWidget(self.search)
        self.table = QTableWidget()
        self.layout.addWidget(self.table, 1)
        run = QPushButton("Run")
        library = QPushButton("Open in library")
        library.setObjectName("secondary")
        toggle = QPushButton("Enable or disable")
        toggle.setObjectName("secondary")
        archive = QPushButton("Archive")
        archive.setObjectName("danger")
        run.clicked.connect(self.run_selected)
        library.clicked.connect(self.open_library)
        toggle.clicked.connect(self.toggle_selected)
        archive.clicked.connect(self.archive_selected)
        self.layout.addWidget(button_row(run, library, toggle, archive))
        self.message = QLabel("")
        self.message.setWordWrap(True)
        self.layout.addWidget(self.message)

    def refresh(self) -> None:
        rows = self.osiris.library.search(self.search.text())
        fill_table(
            self.table,
            ["Name", "Status", "Version", "Last status", "Description"],
            [
                [row["name"], row["status"], row["current_version"], row.get("last_status") or "", row["description"]]
                for row in rows
            ],
            [row["id"] for row in rows],
        )
        if not rows:
            self.message.setText("No automations match that search.")

    def run_selected(self) -> None:
        automation_id = selected_id(self.table)
        record = self.osiris.db.get_automation(automation_id) if automation_id else None
        if record is None:
            self.message.setText("Select an automation first.")
            return
        if not confirm(self, "Run automation", f"Run {record['name']} now?"):
            return
        params = ask_parameters(self, record.get("parameters") or {})
        if params is None:
            return
        self.message.setText("Running...")

        def work():
            return self.osiris.library.run(automation_id, params=params, trigger="manual")

        self.host.start_worker(work, self._show_run, self._show_error)

    def _show_run(self, result: dict) -> None:
        self.message.setText(result.get("summary") or "Finished.")
        self.refresh()

    def _show_error(self, message: str) -> None:
        self.message.setText(message)

    def open_library(self) -> None:
        automation_id = selected_id(self.table)
        self.host.show_page("library")
        self.host.pages["library"].focus(automation_id)

    def toggle_selected(self) -> None:
        automation_id = selected_id(self.table)
        record = self.osiris.db.get_automation(automation_id) if automation_id else None
        if record is None:
            return
        try:
            self.osiris.library.set_enabled(automation_id, not record["enabled"])
        except Exception as error:
            self.message.setText(str(error))
            return
        self.refresh()

    def archive_selected(self) -> None:
        automation_id = selected_id(self.table)
        if not automation_id:
            return
        if not confirm(self, "Archive automation", "Archive this automation and disable its schedules?"):
            return
        self.osiris.library.archive(automation_id)
        self.refresh()


class LibraryPage(Page):
    def __init__(self, osiris, host):
        super().__init__(osiris)
        self.host = host
        self.title("Script library", "Read the source, save edits as a new version, and approve only after a safe test.")
        body = QHBoxLayout()
        self.table = QTableWidget()
        self.table.itemSelectionChanged.connect(self._load_selected)
        body.addWidget(self.table, 1)
        right = QVBoxLayout()
        self.meta = QLabel("Select a script.")
        self.meta.setWordWrap(True)
        self.versions = QComboBox()
        self.versions.currentIndexChanged.connect(self._load_version)
        self.editor = QPlainTextEdit()
        self.editor.setObjectName("code")
        self.result = QLabel("")
        self.result.setWordWrap(True)
        save = QPushButton("Save as new version")
        approve = QPushButton("Approve version")
        reject = QPushButton("Reject version")
        reject.setObjectName("secondary")
        rollback = QPushButton("Roll back to version")
        rollback.setObjectName("secondary")
        test = QPushButton("Run safe test")
        test.setObjectName("secondary")
        run = QPushButton("Run approved")
        save.clicked.connect(self.save_version)
        approve.clicked.connect(self.approve_version)
        reject.clicked.connect(self.reject_version)
        rollback.clicked.connect(self.rollback_version)
        test.clicked.connect(self.test_version)
        run.clicked.connect(self.run_version)
        right.addWidget(self.meta)
        right.addWidget(self.versions)
        right.addWidget(self.editor, 1)
        right.addWidget(button_row(save, test, approve))
        right.addWidget(button_row(reject, rollback, run))
        right.addWidget(self.result)
        body.addLayout(right, 2)
        holder = QWidget()
        holder.setLayout(body)
        self.layout.addWidget(holder, 1)
        self._loading = False

    def refresh(self) -> None:
        current = selected_id(self.table)
        rows = self.osiris.library.search("")
        fill_table(
            self.table,
            ["Name", "Status"],
            [[row["name"], row["status"]] for row in rows],
            [row["id"] for row in rows],
        )
        if current:
            self.focus(current)

    def focus(self, automation_id: str | None) -> None:
        if not automation_id:
            return
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item and item.data(Qt.ItemDataRole.UserRole) == automation_id:
                self.table.setCurrentCell(row, 0)
                break

    def _load_selected(self) -> None:
        automation_id = selected_id(self.table)
        self._loading = True
        self.versions.clear()
        if not automation_id:
            self._loading = False
            return
        record = self.osiris.db.get_automation(automation_id)
        versions = self.osiris.db.list_versions(automation_id)
        for version in versions:
            self.versions.addItem(f"v{version['version']} · {status_text(version['status'])}", version["version"])
        if record and record["current_version"]:
            index = self.versions.findData(record["current_version"])
            if index >= 0:
                self.versions.setCurrentIndex(index)
        self._loading = False
        self._load_version()

    def _load_version(self) -> None:
        if self._loading:
            return
        automation_id = selected_id(self.table)
        version = self.versions.currentData()
        if not automation_id or version is None:
            return
        record = self.osiris.db.get_automation(automation_id)
        row = self.osiris.db.get_version(automation_id, int(version))
        source = self.osiris.library.read_source(automation_id, int(version))
        self.editor.setPlainText(source.get("source") or "")
        validation = row["validation"] if row else {}
        tests = row["test_results"] if row else {}
        warning = source.get("error") or ""
        self.meta.setText(
            f"{record['name']} · {record['description']}\n"
            f"Permissions: {', '.join(record['permissions']) or 'None'}\n"
            f"Validation: {'passed' if validation.get('ok') else 'failed'}\n"
            f"Safe test: {tests.get('status') or 'not run'}\n{warning}"
        )

    def save_version(self) -> None:
        automation_id = selected_id(self.table)
        if not automation_id:
            return
        if not confirm(self, "Save a new version", "This keeps the current approved file and saves your edit as a new version."):
            return
        record = self.osiris.db.get_automation(automation_id)
        source = self.editor.toPlainText()
        self.result.setText("Validating...")

        def work():
            return self.osiris.library.add_version(
                automation_id,
                source=source,
                permissions=list(record["permissions"]),
                parameters=record["parameters"],
            )

        self.host.start_worker(work, self._saved, self._failed)

    def _saved(self, version: dict) -> None:
        state = "ready for approval" if version["validation"].get("ok") else "saved as a draft because validation failed"
        self.result.setText(f"Version {version['version']} is {state}. The previous approved file was not replaced.")
        self.refresh()

    def approve_version(self) -> None:
        automation_id, version = self._current()
        if not automation_id:
            return
        if not confirm(self, "Approve version", "Approve this version and make it the one Run will execute?"):
            return
        try:
            self.osiris.library.approve(automation_id, version)
        except Exception as error:
            self.result.setText(str(error))
            return
        self.result.setText(f"Version {version} is approved.")
        self.refresh()

    def reject_version(self) -> None:
        automation_id, version = self._current()
        if automation_id:
            self.osiris.library.reject(automation_id, version)
            self.result.setText(f"Version {version} was rejected. Older approved versions stay available.")
            self.refresh()

    def rollback_version(self) -> None:
        automation_id, version = self._current()
        if not automation_id:
            return
        if not confirm(self, "Roll back", f"Make version {version} the active approved version?"):
            return
        try:
            self.osiris.library.rollback(automation_id, version)
        except Exception as error:
            self.result.setText(str(error))
            return
        self.result.setText(f"Version {version} is active again.")
        self.refresh()

    def test_version(self) -> None:
        automation_id, version = self._current()
        if not automation_id:
            return
        self.result.setText("Running a safe test...")

        def work():
            return self.osiris.library.run_test(automation_id, version)

        self.host.start_worker(work, lambda result: self.result.setText(result.get("summary") or "Test finished."), self._failed)

    def run_version(self) -> None:
        automation_id = selected_id(self.table)
        record = self.osiris.db.get_automation(automation_id) if automation_id else None
        if record is None:
            return
        if not confirm(self, "Run approved version", f"Run the approved version of {record['name']}?"):
            return
        params = ask_parameters(self, record.get("parameters") or {})
        if params is None:
            return

        def work():
            return self.osiris.library.run(automation_id, params=params)

        self.host.start_worker(work, lambda result: self.result.setText(result.get("summary") or ""), self._failed)

    def _failed(self, message: str) -> None:
        self.result.setText(message)
        self.refresh()

    def _current(self):
        automation_id = selected_id(self.table)
        version = self.versions.currentData()
        if not automation_id or version is None:
            self.result.setText("Select a script version.")
            return None, None
        return automation_id, int(version)


class ProjectsPage(Page):
    def __init__(self, osiris, host):
        super().__init__(osiris)
        self.host = host
        self.title("Projects", "A project is reported as checked only when its own read succeeds.")
        form = QHBoxLayout()
        self.name = QLineEdit()
        self.name.setPlaceholderText("Name")
        self.kind = QComboBox()
        self.kind.addItem("Local Git folder", "local_git")
        self.kind.addItem("GitHub repository", "github")
        self.kind.addItem("Project URL", "url")
        self.location = QLineEdit()
        self.location.setPlaceholderText("Folder path, owner/name, or https URL")
        add = QPushButton("Add")
        add.clicked.connect(self.add_project)
        form.addWidget(self.name)
        form.addWidget(self.kind)
        form.addWidget(self.location, 1)
        form.addWidget(add)
        self.layout.addLayout(form)
        self.table = QTableWidget()
        self.layout.addWidget(self.table)
        check = QPushButton("Check configured projects")
        remove = QPushButton("Remove selected")
        remove.setObjectName("secondary")
        check.clicked.connect(self.check_projects)
        remove.clicked.connect(self.remove_project)
        self.layout.addWidget(button_row(check, remove))
        self.report = QTextEdit()
        self.report.setReadOnly(True)
        self.layout.addWidget(self.report, 1)

    def refresh(self) -> None:
        rows = self.osiris.projects.list_projects()
        fill_table(
            self.table,
            ["Name", "Type", "Location"],
            [[row["name"], row["kind"], row["location"]] for row in rows],
            [row["id"] for row in rows],
        )
        if not rows:
            self.report.setPlainText("No projects are configured, so nothing can be checked yet.")

    def add_project(self) -> None:
        try:
            self.osiris.projects.add_project(self.name.text(), self.kind.currentData(), self.location.text())
        except Exception as error:
            self.report.setPlainText(str(error))
            return
        self.name.clear()
        self.location.clear()
        self.refresh()

    def remove_project(self) -> None:
        project_id = selected_id(self.table)
        if project_id and confirm(self, "Remove project", "Remove this project from monitoring?"):
            self.osiris.projects.remove_project(project_id)
            self.refresh()

    def check_projects(self) -> None:
        self.report.setPlainText("Checking...")

        def work():
            return self.osiris.projects.check_all()

        self.host.start_worker(work, self._show_checks, lambda message: self.report.setPlainText(message))

    def _show_checks(self, results: list[dict]) -> None:
        lines = [summarize_checks(results), ""]
        for result in results:
            lines.append(f"{result['name']} ({result['kind']})")
            if result.get("live_check"):
                lines.append(f"Checked. {result.get('summary', '')}")
                for commit in result.get("commits") or []:
                    lines.append(f"  commit {commit.get('sha', '')} {commit.get('message', '')}")
                for issue in result.get("issues") or []:
                    lines.append(f"  issue {issue.get('title', '')}")
                tests = result.get("tests") or {}
                if tests:
                    lines.append(f"  tests: {tests.get('state')} {tests.get('detail', '')}")
            else:
                lines.append(f"Not checked. {result.get('error') or result.get('summary') or ''}")
                preview = result.get("preview")
                if preview:
                    lines.append("  Simulated preview only. This is not a live result.")
            lines.append("")
        self.report.setPlainText("\n".join(lines).strip())


class MessagesPage(Page):
    def __init__(self, osiris, host):
        super().__init__(osiris)
        self.host = host
        self.title("Email and messages", "Drafts stay on this computer until you confirm an exact recipient. Mock mode does not deliver mail.")
        self.banner = QLabel("")
        self.banner.setWordWrap(True)
        self.layout.addWidget(self.banner)
        self.table = QTableWidget()
        self.table.itemSelectionChanged.connect(self._select_message)
        self.layout.addWidget(self.table)
        self.recipient = QLineEdit()
        self.recipient.setReadOnly(True)
        self.subject = QLineEdit()
        self.body = QPlainTextEdit()
        form = QFormLayout()
        form.addRow("Recipient", self.recipient)
        form.addRow("Subject", self.subject)
        form.addRow("Body", self.body)
        self.layout.addLayout(form)
        prepare = QPushButton("Prepare reply")
        send = QPushButton("Send")
        prepare.clicked.connect(self.prepare_reply)
        send.clicked.connect(self.send_reply)
        self.layout.addWidget(button_row(prepare, send))
        self.result = QLabel("")
        self.result.setWordWrap(True)
        self.layout.addWidget(self.result)
        self.messages = []

    def refresh(self) -> None:
        listing = self.osiris.email.list_messages()
        self.messages = listing.get("messages") or []
        self.banner.setText(listing.get("notice") or listing.get("error") or "")
        fill_table(
            self.table,
            ["From", "Subject", "Priority"],
            [[item.get("from_address"), item.get("subject"), item.get("priority")] for item in self.messages],
            [item.get("id") for item in self.messages],
        )
        if not self.messages:
            self.result.setText("No messages were read from a connected account.")

    def _select_message(self) -> None:
        message = self._message()
        if message:
            self.result.setText(message.get("body") or "")

    def prepare_reply(self) -> None:
        message = self._message()
        if message is None:
            self.result.setText("Select a message first.")
            return
        reply = self.osiris.email.suggest_reply(message["id"])
        self.recipient.setText(reply.get("to") or "")
        self.subject.setText(reply.get("subject") or "")
        self.body.setPlainText(reply.get("body") or "")
        self.osiris.email.save_draft(
            to=self.recipient.text(),
            subject=self.subject.text(),
            body=self.body.toPlainText(),
            in_reply_to=message["id"],
        )
        self.result.setText("Draft saved on this computer. No email was sent.")

    def send_reply(self) -> None:
        message = self._message()
        recipient = self.recipient.text().strip()
        subject = self.subject.text().strip()
        body = self.body.toPlainText()
        if message is None or not recipient:
            self.result.setText("Prepare a reply before sending.")
            return
        if recipient != message.get("from_address"):
            self.result.setText("The recipient does not match the selected message, so nothing was sent.")
            return
        if not confirm(
            self,
            "Send email",
            f"To: {recipient}\nSubject: {subject}\n\n{body}\n\nMock and dry-run modes do not deliver this message.",
        ):
            return
        result = self.osiris.confirm_send(
            to=recipient,
            subject=subject,
            body=body,
            in_reply_to=message.get("id") or "",
            confirmed_to=recipient,
            known_recipients={message.get("from_address") or ""},
        )
        if result.get("delivered"):
            self.result.setText(f"Sent. Provider id: {result.get('provider_id')}.")
        else:
            self.result.setText("No email was sent. " + result.get("detail", ""))

    def _message(self):
        message_id = selected_id(self.table)
        for message in self.messages:
            if message.get("id") == message_id:
                return message
        return None


class SocialPage(Page):
    def __init__(self, osiris, host):
        super().__init__(osiris)
        self.host = host
        self.title("Social media studio", "Generated copy is a draft. Publishing needs approval and a connected account.")
        row = QHBoxLayout()
        self.topic = QLineEdit()
        self.topic.setPlaceholderText("What should the draft be about?")
        generate = QPushButton("Create drafts")
        generate.clicked.connect(self.generate)
        row.addWidget(self.topic, 1)
        row.addWidget(generate)
        self.layout.addLayout(row)
        self.table = QTableWidget()
        self.table.itemSelectionChanged.connect(self._show_draft)
        self.layout.addWidget(self.table)
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.layout.addWidget(self.preview)
        approve = QPushButton("Approve draft")
        publish = QPushButton("Publish")
        approve.clicked.connect(self.approve)
        publish.clicked.connect(self.publish)
        self.layout.addWidget(button_row(approve, publish))
        self.result = QLabel("Nothing has been published.")
        self.result.setWordWrap(True)
        self.layout.addWidget(self.result)

    def refresh(self) -> None:
        drafts = self.osiris.db.list_social_drafts()
        fill_table(
            self.table,
            ["Platform", "Status", "Post id", "Title"],
            [
                [item["platform"], item["status"], item.get("provider_post_id") or "not published", item.get("title")]
                for item in drafts
            ],
            [item["id"] for item in drafts],
        )
        if not drafts:
            self.preview.setPlainText("No drafts yet. Create one from a topic. That does not publish it.")

    def generate(self) -> None:
        topic = self.topic.text().strip()
        if not topic:
            self.result.setText("Enter a topic first. Nothing was published.")
            return
        self.osiris.social.create_drafts(
            topic,
            guidelines=self.osiris.settings.brand_guidelines,
            preferences=self.osiris.settings.content_preferences,
        )
        self.result.setText("Drafts saved. Nothing was published.")
        self.refresh()

    def _show_draft(self) -> None:
        draft = self._draft()
        if draft:
            tags = " ".join(draft.get("hashtags") or [])
            self.preview.setPlainText(f"{draft['body']}\n\n{tags}\n\nStatus: {draft['status']}. Not a published post unless a post id is shown.")

    def approve(self) -> None:
        draft = self._draft()
        if draft is None:
            return
        self.osiris.social.approve_draft(draft["id"])
        self.result.setText("Draft approved. It is still not published.")
        self.refresh()

    def publish(self) -> None:
        draft = self._draft()
        if draft is None:
            return
        if not confirm(self, "Publish post", f"Publish this {draft['platform']} draft?\n\n{draft['body'][:500]}\n\nMock mode does not publish."):
            return
        result = self.osiris.confirm_publish(draft["id"])
        if result.get("published"):
            self.result.setText(f"Published. Post id: {result.get('provider_post_id')}.")
        else:
            self.result.setText("Nothing was published. " + result.get("detail", ""))
        self.refresh()

    def _draft(self):
        draft_id = selected_id(self.table)
        if not draft_id:
            return None
        return self.osiris.db.get_social_draft(draft_id)


class SchedulerPage(Page):
    def __init__(self, osiris, host):
        super().__init__(osiris)
        self.host = host
        self.title("Scheduler", "Schedules are saved on this computer. They run while Osiris is open, and missed times run once at startup.")
        form = QFormLayout()
        self.schedule_name = QLineEdit()
        self.automation = QComboBox()
        self.cadence = QComboBox()
        self.cadence.addItems(["daily", "weekly", "interval"])
        self.time_of_day = QLineEdit("08:00")
        self.interval = QSpinBox()
        self.interval.setRange(5, 10080)
        self.interval.setValue(60)
        self.retries = QSpinBox()
        self.retries.setRange(0, 5)
        self.retries.setValue(2)
        self.days = []
        day_row = QHBoxLayout()
        for index, name in enumerate(["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]):
            box = QCheckBox(name)
            box.setChecked(index < 5)
            self.days.append(box)
            day_row.addWidget(box)
        form.addRow("Name", self.schedule_name)
        form.addRow("Automation", self.automation)
        form.addRow("Repeat", self.cadence)
        form.addRow("Time", self.time_of_day)
        form.addRow("Interval minutes", self.interval)
        form.addRow("Retries", self.retries)
        form.addRow("Days", _wrap(day_row))
        self.layout.addLayout(form)
        save = QPushButton("Save schedule")
        save.clicked.connect(self.save_schedule)
        toggle = QPushButton("Enable or disable")
        toggle.setObjectName("secondary")
        toggle.clicked.connect(self.toggle_schedule)
        self.layout.addWidget(button_row(save, toggle))
        self.table = QTableWidget()
        self.layout.addWidget(self.table, 1)
        self.result = QLabel(f"Timezone: {osiris.settings.timezone}")
        self.result.setWordWrap(True)
        self.layout.addWidget(self.result)

    def refresh(self) -> None:
        current = self.automation.currentData()
        self.automation.clear()
        for record in self.osiris.library.search(""):
            if record["status"] in {"approved", "disabled"}:
                self.automation.addItem(record["name"], record["id"])
        if current is not None:
            index = self.automation.findData(current)
            if index >= 0:
                self.automation.setCurrentIndex(index)
        rows = self.osiris.db.list_schedules()
        fill_table(
            self.table,
            ["Name", "Repeat", "Next run", "Last status", "Enabled"],
            [
                [row["name"], row["cadence"], describe_next(row), row.get("last_status") or "not run", "yes" if row["enabled"] else "no"]
                for row in rows
            ],
            [row["id"] for row in rows],
        )
        if not rows:
            self.result.setText(f"No schedules yet. Timezone: {self.osiris.settings.timezone}. Osiris must be open for a schedule to fire.")

    def save_schedule(self) -> None:
        automation_id = self.automation.currentData()
        if not automation_id:
            self.result.setText("Approve an automation before scheduling it.")
            return
        days = [index for index, box in enumerate(self.days) if box.isChecked()]
        try:
            self.osiris.scheduler.add(
                automation_id=automation_id,
                name=self.schedule_name.text() or "Scheduled automation",
                cadence=self.cadence.currentText(),
                time_of_day=self.time_of_day.text(),
                days=days,
                interval_minutes=self.interval.value(),
                retry_limit=self.retries.value(),
                timezone_name=self.osiris.settings.timezone,
            )
        except Exception as error:
            self.result.setText(str(error))
            return
        self.result.setText("Schedule saved. It will run while Osiris is open.")
        self.refresh()

    def toggle_schedule(self) -> None:
        schedule_id = selected_id(self.table)
        schedule = self.osiris.db.get_schedule(schedule_id) if schedule_id else None
        if schedule is None:
            return
        self.osiris.scheduler.set_enabled(schedule_id, not schedule["enabled"])
        self.refresh()


class HistoryPage(Page):
    def __init__(self, osiris, host):
        super().__init__(osiris)
        self.host = host
        self.title("Execution history", "Summaries come from tool results. Secrets are removed before they are stored.")
        self.filter = QComboBox()
        self.filter.addItem("All", "")
        self.filter.addItem("Failed", "failed")
        self.filter.addItem("Completed", "completed")
        self.filter.addItem("Running", "running")
        self.filter.currentIndexChanged.connect(self.refresh)
        self.layout.addWidget(self.filter)
        self.table = QTableWidget()
        self.table.itemSelectionChanged.connect(self._show)
        self.layout.addWidget(self.table)
        self.detail = QPlainTextEdit()
        self.detail.setReadOnly(True)
        self.layout.addWidget(self.detail, 1)

    def refresh(self) -> None:
        status = self.filter.currentData()
        rows = self.osiris.db.list_executions(status or None, limit=200)
        self._rows = {row["id"]: row for row in rows}
        if status == "failed" and not rows:
            self.detail.setPlainText("No failed automation tasks.")
        fill_table(
            self.table,
            ["When", "Name", "Trigger", "Status", "Summary"],
            [
                [row["started_at"][:19], row.get("name") or "", row["trigger"], row["status"], (row.get("summary") or "")[:140]]
                for row in rows
            ],
            [row["id"] for row in rows],
        )

    def _show(self) -> None:
        execution_id = selected_id(self.table)
        row = getattr(self, "_rows", {}).get(execution_id)
        if row is None:
            return
        text = f"{row.get('summary') or ''}\n\nError: {row.get('error') or 'None'}"
        log_path = row.get("log_path") or ""
        if log_path:
            try:
                from pathlib import Path

                text += "\n\nLog\n" + Path(log_path).read_text(encoding="utf-8")[:8000]
            except OSError:
                text += "\n\nThe log file could not be read."
        self.detail.setPlainText(text)


class IntegrationsPage(Page):
    def __init__(self, osiris, host):
        super().__init__(osiris)
        self.host = host
        self.title("Integrations", "Start in mock or dry-run. Live mode still asks before a send or a post, and it never stores a password.")
        self.rows = {}
        self.form = QFormLayout()
        self.layout.addLayout(self.form)
        save = QPushButton("Save integration modes")
        save.clicked.connect(self.save)
        self.layout.addWidget(button_row(save))
        self.result = QLabel("")
        self.result.setWordWrap(True)
        self.layout.addWidget(self.result)
        self.layout.addStretch(1)

    def refresh(self) -> None:
        while self.form.rowCount():
            self.form.removeRow(0)
        self.rows = {}
        for item in self.osiris.db.list_integrations():
            picker = QComboBox()
            picker.addItems(["mock", "dry_run", "live"])
            picker.setCurrentText(item["mode"])
            self.rows[item["provider"]] = picker
            self.form.addRow(f"{item['display_name']} · {item['status']}", picker)

    def save(self) -> None:
        for provider, picker in self.rows.items():
            self.osiris.set_integration_mode(provider, picker.currentText())
        self.result.setText("Modes saved. Mock and dry-run do not contact accounts. Live email and social publishing stay unavailable until an official connection exists.")
        self.refresh()


class SettingsPage(Page):
    def __init__(self, osiris, host):
        super().__init__(osiris)
        self.host = host
        self.title("Settings", "The LM Studio address and model are saved here. API keys stay in environment variables.")
        self.url = QLineEdit()
        self.model = QLineEdit()
        self.timeout = QSpinBox()
        self.timeout.setRange(1, 600)
        self.retries = QSpinBox()
        self.retries.setRange(0, 5)
        self.timezone = QLineEdit()
        self.poll = QSpinBox()
        self.poll.setRange(5, 3600)
        self.run_timeout = QSpinBox()
        self.run_timeout.setRange(1, 300)
        self.directories = QPlainTextEdit()
        self.brand = QPlainTextEdit()
        self.preferences = QPlainTextEdit()
        form = QFormLayout()
        form.addRow("LM Studio URL", self.url)
        form.addRow("Model name", self.model)
        form.addRow("Request timeout seconds", self.timeout)
        form.addRow("Retry limit", self.retries)
        form.addRow("Timezone", self.timezone)
        form.addRow("Scheduler poll seconds", self.poll)
        form.addRow("Script timeout seconds", self.run_timeout)
        form.addRow("Allowed folders, one per line", self.directories)
        form.addRow("Brand guidelines", self.brand)
        form.addRow("Content preferences", self.preferences)
        self.layout.addLayout(form)
        save = QPushButton("Save settings")
        test = QPushButton("Test LM Studio connection")
        test.setObjectName("secondary")
        resume = QPushButton("Resume automations")
        resume.setObjectName("secondary")
        save.clicked.connect(self.save)
        test.clicked.connect(self.test_connection)
        resume.clicked.connect(self.resume)
        self.layout.addWidget(button_row(save, test, resume))
        self.result = QLabel("OSIRIS_LM_API_KEY is read from the environment when you set it. The local placeholder is not a credential.")
        self.result.setWordWrap(True)
        self.layout.addWidget(self.result)
        self.layout.addStretch(1)

    def refresh(self) -> None:
        settings = self.osiris.settings
        self.url.setText(settings.lm_base_url)
        self.model.setText(settings.lm_model)
        self.model.setPlaceholderText("Blank uses the first model loaded in LM Studio")
        self.timeout.setValue(int(settings.timeout_seconds))
        self.retries.setValue(int(settings.max_retries))
        self.timezone.setText(settings.timezone)
        self.poll.setValue(int(settings.scheduler_poll_seconds))
        self.run_timeout.setValue(int(settings.execution_timeout_seconds))
        self.directories.setPlainText("\n".join(settings.allowed_directories))
        self.brand.setPlainText(settings.brand_guidelines)
        self.preferences.setPlainText(settings.content_preferences)
        if self.osiris.emergency_stopped():
            self.result.setText("Automations are stopped.")

    def save(self) -> None:
        from osiris.config import Settings

        current = self.osiris.settings
        try:
            updated = Settings(
                lm_base_url=self.url.text().strip(),
                lm_model=self.model.text().strip(),
                timeout_seconds=float(self.timeout.value()),
                max_retries=self.retries.value(),
                temperature=current.temperature,
                planner_temperature=current.planner_temperature,
                timezone=self.timezone.text().strip(),
                scheduler_poll_seconds=self.poll.value(),
                execution_timeout_seconds=self.run_timeout.value(),
                max_output_chars=current.max_output_chars,
                max_script_chars=current.max_script_chars,
                memory_limit_mb=current.memory_limit_mb,
                allowed_directories=[line.strip() for line in self.directories.toPlainText().splitlines() if line.strip()],
                brand_guidelines=self.brand.toPlainText(),
                content_preferences=self.preferences.toPlainText(),
            )
            self.osiris.save_settings(updated)
        except Exception as error:
            self.result.setText(str(error))
            return
        self.host.restart_scheduler()
        self.result.setText("Settings saved. The API key was not written to disk.")

    def test_connection(self) -> None:
        self.result.setText("Connecting...")

        def work():
            return self.osiris.llm.check_connection()

        def show(result: dict):
            if result["ok"]:
                self.result.setText("Server connected. Models: " + ", ".join(result["models"]))
                self.host.set_status("● Server connected", "#69D99A")
            else:
                self.result.setText(result["error"] or "Server unavailable")
                self.host.set_status("● Server unavailable", "#FF7777")

        self.host.start_worker(work, show, lambda message: self.result.setText(message))

    def resume(self) -> None:
        if confirm(self, "Resume automations", "Allow approved automations and schedules to run again?"):
            self.osiris.resume()
            self.result.setText("Automations can run again.")
            self.host.set_status("● Ready", "#69D99A")


def _wrap(layout: QHBoxLayout) -> QWidget:
    widget = QWidget()
    widget.setLayout(layout)
    return widget
