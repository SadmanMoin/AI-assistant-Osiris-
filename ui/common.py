"""Shared widgets and background work for the desktop UI."""

from __future__ import annotations

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from osiris.audit.redact import redact_text
from ui.theme import STATUS_COLORS, status_text

NAV = [
    ("dashboard", "Dashboard"),
    ("assistant", "AI Assistant"),
    ("automations", "Automations"),
    ("library", "Script Library"),
    ("projects", "Projects"),
    ("messages", "Email and Messages"),
    ("social", "Social Media Studio"),
    ("scheduler", "Scheduler"),
    ("history", "Execution History"),
    ("integrations", "Integrations"),
    ("settings", "Settings"),
]


class CallWorker(QThread):
    succeeded = Signal(object)
    failed = Signal(str)

    def __init__(self, fn, secrets: list[str]):
        super().__init__()
        self.fn = fn
        self.secrets = secrets

    def run(self):
        try:
            self.succeeded.emit(self.fn())
        except Exception as error:
            self.failed.emit(redact_text(str(error), self.secrets))


class Page(QWidget):
    def __init__(self, osiris, parent=None):
        super().__init__(parent)
        self.osiris = osiris
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(4, 4, 4, 4)
        self.layout.setSpacing(12)

    def refresh(self) -> None:
        return None

    def title(self, text: str, subtitle: str = "") -> None:
        heading = QLabel(text)
        heading.setObjectName("title")
        self.layout.addWidget(heading)
        if subtitle:
            note = QLabel(subtitle)
            note.setObjectName("subtitle")
            note.setWordWrap(True)
            self.layout.addWidget(note)


def card(title: str, value: str, note: str = "") -> QFrame:
    frame = QFrame()
    frame.setObjectName("card")
    layout = QVBoxLayout(frame)
    label = QLabel(title)
    label.setObjectName("muted")
    number = QLabel(value)
    number.setObjectName("stat")
    layout.addWidget(label)
    layout.addWidget(number)
    if note:
        extra = QLabel(note)
        extra.setObjectName("subtitle")
        extra.setWordWrap(True)
        layout.addWidget(extra)
    return frame


def fill_table(table: QTableWidget, headers: list[str], rows: list[list], ids: list | None = None) -> None:
    table.clear()
    table.setColumnCount(len(headers))
    table.setHorizontalHeaderLabels(headers)
    table.setRowCount(len(rows))
    table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
    table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
    table.verticalHeader().setVisible(False)
    for row_index, row in enumerate(rows):
        for column, value in enumerate(row):
            item = QTableWidgetItem("" if value is None else str(value))
            if column == 0 and ids is not None:
                item.setData(Qt.ItemDataRole.UserRole, ids[row_index])
            if column < len(headers) and headers[column].lower() in {"status", "last status", "mode"}:
                color = STATUS_COLORS.get(str(value).lower(), "#F1F3F5")
                item.setForeground(QBrush(QColor(color)))
                item.setText(status_text(str(value)))
            table.setItem(row_index, column, item)
    header = table.horizontalHeader()
    header.setStretchLastSection(True)
    if not rows:
        table.setRowCount(0)


def selected_id(table: QTableWidget):
    row = table.currentRow()
    if row < 0:
        return None
    item = table.item(row, 0)
    if item is None:
        return None
    return item.data(Qt.ItemDataRole.UserRole)


def confirm(parent, title: str, text: str) -> bool:
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Warning)
    box.setWindowTitle(title)
    box.setText(text)
    box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
    box.setDefaultButton(QMessageBox.StandardButton.No)
    return box.exec() == QMessageBox.StandardButton.Yes


def ask_parameters(parent, schema: dict) -> dict | None:
    if not schema:
        return {}
    dialog = QDialog(parent)
    dialog.setWindowTitle("Automation inputs")
    dialog.resize(420, 240)
    form = QFormLayout(dialog)
    fields = {}
    for name, spec in schema.items():
        edit = QLineEdit()
        if isinstance(spec, dict):
            edit.setPlaceholderText(str(spec.get("label") or name))
            if spec.get("default") is not None:
                edit.setText(str(spec["default"]))
        fields[name] = edit
        label = spec.get("label") if isinstance(spec, dict) else name
        form.addRow(str(label), edit)
    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    form.addRow(buttons)
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return None
    return {name: edit.text().strip() for name, edit in fields.items()}


def button_row(*buttons: QPushButton) -> QWidget:
    widget = QWidget()
    layout = QHBoxLayout(widget)
    layout.setContentsMargins(0, 0, 0, 0)
    for item in buttons:
        layout.addWidget(item)
    layout.addStretch(1)
    return widget
