"""Osiris AI Assistant.

Run this file to open the desktop app. Chat still uses LM Studio.
Automation plans, scripts, and schedules live beside that chat.
"""

import sys

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import QApplication

from osiris.chat import escape_html
from osiris.config import Settings
from osiris.llm.client import LMStudioClient
from ui.main_window import MainWindow


class AIWorker(QThread):
    """Background chat request, preserved for the original send flow."""

    finished_response = Signal(str)
    error_response = Signal(str)

    def __init__(self, messages):
        super().__init__()
        self.messages = messages

    def run(self):
        try:
            settings = Settings()
            client = LMStudioClient(settings)
            answer = client.complete_chat(self.messages)
            self.finished_response.emit(answer or "AI কোনো উত্তর দেয়নি।")
        except Exception as error:
            self.error_response.emit(str(error))


class PersonalAssistant(MainWindow):
    """Window name kept from the original single-page assistant."""

    @staticmethod
    def escape_html(text: str) -> str:
        return escape_html(text)


def main() -> None:
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    window = PersonalAssistant()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
