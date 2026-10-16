"""Main window. The original chat stays on the AI Assistant page."""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from osiris.app import OsirisApp
from osiris.chat import escape_html
from ui.assistant import AssistantPage
from ui.common import NAV, CallWorker, confirm
from ui.pages import (
    AutomationsPage,
    DashboardPage,
    HistoryPage,
    IntegrationsPage,
    LibraryPage,
    MessagesPage,
    ProjectsPage,
    SchedulerPage,
    SettingsPage,
    SocialPage,
)
from ui.theme import STYLESHEET


class MainWindow(QMainWindow):
    def __init__(self, osiris: OsirisApp | None = None):
        super().__init__()
        self.osiris = osiris or OsirisApp()
        self._workers: list[CallWorker] = []
        self._ticking = False
        self.setWindowTitle("Osiris AI Assistant")
        self.resize(1180, 760)
        self.setMinimumSize(900, 600)
        self.setStyleSheet(STYLESHEET)

        root = QWidget()
        self.setCentralWidget(root)
        layout = QHBoxLayout(root)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(14)

        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(220)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(14, 18, 14, 14)
        logo = QLabel("✦ OSIRIS")
        logo.setFont(QFont("Segoe UI", 16, QFont.Weight.Bold))
        side.addWidget(logo)
        tagline = QLabel("Personal automation\nLocal model · Private")
        tagline.setObjectName("subtitle")
        side.addWidget(tagline)
        side.addSpacing(12)

        self.stack = QStackedWidget()
        self.pages = {}
        self.buttons = {}
        group = QButtonGroup(self)
        group.setExclusive(True)
        constructors = {
            "dashboard": lambda: DashboardPage(self.osiris, self),
            "assistant": lambda: AssistantPage(self.osiris, self),
            "automations": lambda: AutomationsPage(self.osiris, self),
            "library": lambda: LibraryPage(self.osiris, self),
            "projects": lambda: ProjectsPage(self.osiris, self),
            "messages": lambda: MessagesPage(self.osiris, self),
            "social": lambda: SocialPage(self.osiris, self),
            "scheduler": lambda: SchedulerPage(self.osiris, self),
            "history": lambda: HistoryPage(self.osiris, self),
            "integrations": lambda: IntegrationsPage(self.osiris, self),
            "settings": lambda: SettingsPage(self.osiris, self),
        }
        for key, label in NAV:
            button = QPushButton(label)
            button.setObjectName("nav")
            button.setCheckable(True)
            button.clicked.connect(lambda checked=False, name=key: self.show_page(name))
            group.addButton(button)
            side.addWidget(button)
            self.buttons[key] = button
            page = constructors[key]()
            self.pages[key] = page
            self.stack.addWidget(page)

        side.addStretch(1)
        self.stop_button = QPushButton("Stop all")
        self.stop_button.setObjectName("danger")
        self.stop_button.clicked.connect(self.stop_all)
        side.addWidget(self.stop_button)
        self.status_label = QLabel("● Connecting...")
        self.status_label.setObjectName("subtitle")
        self.status_label.setWordWrap(True)
        side.addWidget(self.status_label)

        layout.addWidget(sidebar)
        layout.addWidget(self.stack, 1)
        self._alive = True
        self._started = False
        self.scheduler_timer = QTimer(self)
        self.scheduler_timer.timeout.connect(self.run_due_schedules)
        self.show_page("dashboard")

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self._started:
            return
        self._started = True
        self.restart_scheduler()
        QTimer.singleShot(700, self.check_server)
        QTimer.singleShot(1200, self.run_due_schedules)

    def closeEvent(self, event) -> None:
        self._alive = False
        self.scheduler_timer.stop()
        super().closeEvent(event)

    def show_page(self, key: str) -> None:
        page = self.pages[key]
        self.stack.setCurrentWidget(page)
        button = self.buttons[key]
        button.setChecked(True)
        refresher = getattr(page, "refresh", None)
        if callable(refresher):
            refresher()

    def refresh_current(self) -> None:
        page = self.stack.currentWidget()
        refresher = getattr(page, "refresh", None)
        if callable(refresher):
            refresher()

    def set_status(self, text: str, color: str) -> None:
        if not self._alive:
            return
        self.status_label.setText(text)
        self.status_label.setStyleSheet(f"color: {color};")

    def check_server(self) -> None:
        self.set_status("● Connecting...", "#A0A7B4")

        def work():
            return self.osiris.llm.check_connection()

        def done(result: dict) -> None:
            if result.get("ok"):
                self.set_status("● Server connected", "#69D99A")
            else:
                self.set_status("● Server unavailable", "#FF7777")

        self.start_worker(work, done, lambda _message: self.set_status("● Server unavailable", "#FF7777"))

    def start_worker(self, fn, on_success, on_error) -> None:
        worker = CallWorker(fn, self.osiris.settings.secret_values())
        self._workers.append(worker)
        worker.succeeded.connect(on_success)
        worker.failed.connect(on_error)
        worker.finished.connect(lambda worker=worker: self._release_worker(worker))
        worker.start()

    def _release_worker(self, worker: CallWorker) -> None:
        if worker in self._workers:
            self._workers.remove(worker)
        worker.deleteLater()

    def run_due_schedules(self) -> None:
        if self._ticking:
            return
        self._ticking = True

        def work():
            return self.osiris.scheduler.tick()

        def done(_results) -> None:
            self._ticking = False
            if self._alive and self.stack.currentWidget() is self.pages["dashboard"]:
                self.pages["dashboard"].refresh()

        def failed(_message: str) -> None:
            self._ticking = False

        self.start_worker(work, done, failed)

    def restart_scheduler(self) -> None:
        seconds = int(self.osiris.settings.scheduler_poll_seconds)
        self.scheduler_timer.start(max(5, seconds) * 1000)

    def stop_all(self) -> None:
        if not confirm(self, "Stop all automations", "Stop running automations and block new runs until you resume them?"):
            return
        self.osiris.stop_all()
        self.set_status("● Automations stopped", "#FF8585")
        self.refresh_current()

    def new_chat(self) -> None:
        self.show_page("assistant")
        self.pages["assistant"].new_chat()

    @staticmethod
    def escape_html(text: str) -> str:
        return escape_html(text)
