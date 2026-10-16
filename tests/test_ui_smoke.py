import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent))
import support

from PySide6.QtWidgets import QApplication

from gui import PersonalAssistant
from ui.common import NAV
from ui.main_window import MainWindow


class UiSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])

    def test_every_page_opens_and_refreshes(self):
        osiris, folder = support.make_app()
        try:
            window = MainWindow(osiris)
            self.assertEqual(window.windowTitle(), "Osiris AI Assistant")
            self.assertIn("helpful personal AI assistant", osiris.chat.messages[0]["content"])
            for key, _label in NAV:
                window.show_page(key)
                window.pages[key].refresh()
            window.buttons["projects"].click()
            self.assertIs(window.stack.currentWidget(), window.pages["projects"])
            window.new_chat()
            self.assertEqual(len(osiris.chat.messages), 1)
            self.assertTrue(issubclass(PersonalAssistant, MainWindow))
            self.assertEqual(PersonalAssistant.escape_html("<b>"), "&lt;b&gt;")
            window.pages["assistant"].input.setText("What is a Python decorator?")
            self.assertEqual(window.pages["assistant"].input.text(), "What is a Python decorator?")
            window.close()
        finally:
            support.cleanup(folder)


if __name__ == "__main__":
    unittest.main()
