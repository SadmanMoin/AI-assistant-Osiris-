"""Chat page. Ordinary questions keep the original LM Studio path."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSplitter,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from osiris.chat import escape_html
from ui.common import confirm

_WELCOME = """
<div style="text-align:center; margin-top:48px;">
    <h1 style="color:#FFFFFF;">Hello!</h1>
    <p style="color:#A0A7B4;">Welcome to your personal AI assistant.</p>
    <p style="color:#A0A7B4;">Ask me anything about Python, coding, learning, or your projects.</p>
    <p style="color:#A0A7B4;">Automation commands can check projects, draft email, or prepare a script for approval.</p>
</div>
"""


class AssistantPage(QWidget):
    def __init__(self, osiris, host):
        super().__init__()
        self.osiris = osiris
        self.host = host
        self.plan = None
        self.busy = False

        splitter = QSplitter()
        chat_side = QWidget()
        chat_layout = QVBoxLayout(chat_side)
        chat_layout.setContentsMargins(0, 0, 0, 0)
        title = QLabel("Osiris AI Assistant")
        title.setObjectName("title")
        subtitle = QLabel("Your local AI companion · Powered by LM Studio")
        subtitle.setObjectName("subtitle")
        chat_layout.addWidget(title)
        chat_layout.addWidget(subtitle)

        self.transcript = QTextEdit()
        self.transcript.setObjectName("chat")
        self.transcript.setReadOnly(True)
        self.transcript.setHtml(_WELCOME)
        chat_layout.addWidget(self.transcript, 1)

        input_row = QHBoxLayout()
        self.input = QLineEdit()
        self.input.setPlaceholderText("Message your AI assistant...")
        self.input.returnPressed.connect(self.send_message)
        self.send_button = QPushButton("Send ➤")
        self.send_button.clicked.connect(self.send_message)
        self.new_chat_button = QPushButton("＋ New Chat")
        self.new_chat_button.setObjectName("secondary")
        self.new_chat_button.clicked.connect(self.new_chat)
        input_row.addWidget(self.input, 1)
        input_row.addWidget(self.send_button)
        input_row.addWidget(self.new_chat_button)
        chat_layout.addLayout(input_row)

        plan_side = QWidget()
        plan_layout = QVBoxLayout(plan_side)
        plan_layout.setContentsMargins(0, 0, 0, 0)
        plan_title = QLabel("Task plan")
        plan_title.setObjectName("section")
        plan_layout.addWidget(plan_title)
        self.plan_view = QTextEdit()
        self.plan_view.setReadOnly(True)
        self.plan_view.setPlainText("No pending action. A normal chat does not change your accounts or files.")
        plan_layout.addWidget(self.plan_view, 1)
        self.action_row = QHBoxLayout()
        plan_layout.addLayout(self.action_row)

        splitter.addWidget(chat_side)
        splitter.addWidget(plan_side)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addWidget(splitter)

    def refresh(self) -> None:
        return None

    def new_chat(self) -> None:
        if self.busy:
            return
        self.osiris.chat.reset()
        self.plan = None
        self.transcript.clear()
        self.transcript.append("<h2>New Chat</h2><p>What would you like help with?</p>")
        self.plan_view.setPlainText("No pending action. A normal chat does not change your accounts or files.")
        self._set_actions()
        self.host.set_status("● Ready", "#69D99A")

    def send_message(self) -> None:
        question = self.input.text().strip()
        if not question or self.busy:
            return
        self.input.clear()
        self._append("You", question, "#91A2FF")
        self.osiris.chat.add_user(question)
        self._set_busy(True, "Thinking...")

        def work():
            prepared = self.osiris.prepare_turn(question)
            if prepared["kind"] == "chat":
                answer = self.osiris.answer_chat()
                self.osiris.chat.add_assistant(answer)
                return {"kind": "chat", "reply": answer}
            self.osiris.chat.add_assistant(prepared["reply"])
            return prepared

        self.host.start_worker(work, self._on_ready, self._on_error)

    def _on_ready(self, result: dict) -> None:
        self._append("Osiris", result.get("reply") or "", "#F1F3F5")
        if result.get("kind") == "plan":
            self.plan = result["plan"]
            self.plan_view.setPlainText(_describe_plan(self.plan))
            self._set_actions(self.plan)
            self.host.set_status("● Waiting for you", "#E2B657")
        else:
            self.plan = None
            self.plan_view.setPlainText("No pending action. A normal chat does not change your accounts or files.")
            self._set_actions()
            self.host.set_status("● Ready", "#69D99A")
        self._set_busy(False, "Send ➤")

    def _on_error(self, message: str) -> None:
        self.osiris.chat.fail_last_turn()
        self._append("Error", message, "#FF8585")
        self._set_busy(False, "Send ➤")
        self.host.set_status("● Check connection", "#FF7777")

    def _run_current(self) -> None:
        plan = self.plan
        if not plan or self.busy:
            return
        intent = plan["intent"]
        if intent == "stop_all":
            if confirm(self, "Stop all automations", "Stop every running automation and block new runs?"):
                self.osiris.stop_all()
                self._show_result("Automations are stopped. Resume them from Settings when you want schedules to run again.")
                self.host.set_status("● Automations stopped", "#FF8585")
            return
        if intent == "run_automation":
            record = self.osiris.db.get_automation(plan["automation_id"])
            if record is None:
                self._show_result("That saved automation is no longer available.")
                return
            effects = "\n".join(plan.get("side_effects") or ["Reads data."])
            if not confirm(self, "Run automation", f"Run {record['name']}?\n\n{effects}"):
                return
            from ui.common import ask_parameters

            params = ask_parameters(self, record.get("parameters") or {})
            if params is None:
                return
        else:
            params = dict(plan.get("parameters") or {})
        self._set_busy(True, "Running...")
        self.host.set_status("● Running", "#8FB0FF")

        def work():
            return self.osiris.perform(plan, params)

        self.host.start_worker(work, self._on_result, self._on_error)

    def _save_script(self) -> None:
        if not self.plan or not self.plan.get("new_script"):
            return
        self._set_busy(True, "Checking...")
        plan = self.plan

        def work():
            return self.osiris.perform(plan, {})

        self.host.start_worker(work, self._on_result, self._on_error)

    def _use_saved(self) -> None:
        if not self.plan or not self.plan.get("reuse_candidate_id"):
            return
        record = self.osiris.db.get_automation(self.plan["reuse_candidate_id"])
        if record is None:
            return
        self.plan = {
            **self.plan,
            "intent": "run_automation",
            "automation_id": record["id"],
            "permissions": record["permissions"],
            "reply": f"Ready to run the saved workflow '{record['name']}'.",
        }
        from osiris.automation.api import effects_for

        self.plan["side_effects"] = effects_for(record["permissions"])
        self.plan_view.setPlainText(_describe_plan(self.plan))
        self._set_actions(self.plan)

    def _on_result(self, result: dict) -> None:
        summary = result.get("summary") or "Finished."
        self._show_result(summary)
        self._set_busy(False, "Send ➤")
        color = "#69D99A" if result.get("ok", True) and result.get("status") != "failed" else "#FF8585"
        self.host.set_status("● Ready" if color == "#69D99A" else "● Needs attention", color)
        self.host.refresh_current()

    def _show_result(self, summary: str) -> None:
        self._append("Osiris", summary, "#F1F3F5")
        current = self.plan_view.toPlainText()
        self.plan_view.setPlainText(current + "\n\nResult\n" + summary)

    def _append(self, who: str, text: str, color: str) -> None:
        safe = escape_html(text or "").replace("\n", "<br>")
        self.transcript.append(f'<p style="color:{color};"><b>{who}:</b><br>{safe}</p>')
        bar = self.transcript.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _set_busy(self, busy: bool, send_label: str) -> None:
        self.busy = busy
        self.input.setEnabled(not busy)
        self.send_button.setEnabled(not busy)
        self.new_chat_button.setEnabled(not busy)
        self.send_button.setText(send_label)
        if not busy:
            self.input.setFocus()

    def _set_actions(self, plan: dict | None = None) -> None:
        while self.action_row.count():
            item = self.action_row.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        if not plan:
            return
        intent = plan["intent"]
        if intent == "create_script" and plan.get("reuse_candidate_id"):
            use_saved = QPushButton("Use saved script")
            use_saved.clicked.connect(self._use_saved)
            self.action_row.addWidget(use_saved)
        if intent == "create_script":
            save = QPushButton("Save for review")
            save.clicked.connect(self._save_script)
            self.action_row.addWidget(save)
        elif intent == "stop_all":
            stop = QPushButton("Stop all automations")
            stop.setObjectName("danger")
            stop.clicked.connect(self._run_current)
            self.action_row.addWidget(stop)
        elif intent in {"run_automation", "monitor_projects", "draft_email", "draft_social", "list_failed"}:
            run = QPushButton("Run")
            run.clicked.connect(self._run_current)
            self.action_row.addWidget(run)
        self.action_row.addStretch(1)


def _describe_plan(plan: dict) -> str:
    lines = [
        f"Intent: {plan.get('intent', '')}",
        "",
        plan.get("reply") or "",
        "",
        "Permissions:",
    ]
    permissions = plan.get("permissions") or []
    lines.extend(f"- {item}" for item in permissions)
    if not permissions:
        lines.append("- None")
    lines.append("")
    lines.append("Expected effects:")
    effects = plan.get("side_effects") or ["No external account is changed."]
    lines.extend(f"- {item}" for item in effects)
    if plan.get("notice"):
        lines.extend(["", plan["notice"]])
    if plan.get("new_script"):
        lines.extend(["", "A script can be saved for review. It stays disabled until you approve it."])
    lines.extend(["", "External content cannot authorize a send, a post, or a permission change."])
    return "\n".join(lines)
