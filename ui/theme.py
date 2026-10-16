"""Visual style carried over from the original Osiris chat window."""

STYLESHEET = """
QMainWindow, QWidget {
    background-color: #111318;
    color: #F1F3F5;
    font-family: "Segoe UI";
    font-size: 14px;
}
QFrame#sidebar {
    background-color: #191C23;
    border: none;
    border-radius: 14px;
}
QFrame#card {
    background-color: #191C23;
    border: 1px solid #2A3142;
    border-radius: 14px;
}
QLabel#title {
    font-size: 24px;
    font-weight: 700;
    color: #FFFFFF;
}
QLabel#section {
    font-size: 16px;
    font-weight: 700;
    color: #FFFFFF;
}
QLabel#subtitle, QLabel#muted {
    color: #A0A7B4;
    font-size: 12px;
}
QLabel#stat {
    font-size: 28px;
    font-weight: 700;
    color: #FFFFFF;
}
QTextEdit#chat {
    background-color: #111318;
    border: none;
    padding: 8px;
    selection-background-color: #4658A8;
}
QTextEdit, QPlainTextEdit, QListWidget, QTableWidget {
    background-color: #141820;
    border: 1px solid #2A3142;
    border-radius: 10px;
    selection-background-color: #4658A8;
}
QPlainTextEdit#code {
    font-family: Consolas, "Cascadia Mono", monospace;
    font-size: 13px;
}
QLineEdit, QComboBox, QSpinBox {
    background-color: #222630;
    border: 1px solid #353B48;
    border-radius: 10px;
    padding: 8px 10px;
    color: white;
    min-height: 20px;
}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QPlainTextEdit:focus, QTextEdit:focus {
    border: 1px solid #8293FF;
}
QPushButton {
    background-color: #6678F5;
    color: white;
    border: none;
    border-radius: 10px;
    padding: 10px 14px;
    font-weight: 700;
}
QPushButton:hover { background-color: #8290FF; }
QPushButton:disabled { background-color: #41465A; color: #B0B4C0; }
QPushButton#nav {
    text-align: left;
    background-color: transparent;
    color: #C9D0DC;
    border-radius: 8px;
    padding: 9px 12px;
    font-weight: 600;
}
QPushButton#nav:hover { background-color: #242A38; }
QPushButton#nav:checked { background-color: #2C3550; color: white; }
QPushButton#secondary { background-color: #2A3142; }
QPushButton#secondary:hover { background-color: #364056; }
QPushButton#danger { background-color: #8E3D45; }
QPushButton#danger:hover { background-color: #B5525B; }
QHeaderView::section {
    background-color: #191C23;
    color: #A0A7B4;
    border: none;
    padding: 8px;
    font-weight: 600;
}
QTableWidget { gridline-color: #2A3142; }
QScrollArea { border: none; background: transparent; }
QScrollBar:vertical { background: #191C23; width: 10px; margin: 0; }
QScrollBar::handle:vertical { background: #3A4256; border-radius: 5px; }
QCheckBox { spacing: 8px; }
QComboBox QAbstractItemView {
    background-color: #222630;
    color: white;
    selection-background-color: #4658A8;
}
"""

STATUS_COLORS = {
    "draft": "#A0A7B4",
    "pending_approval": "#E2B657",
    "pending approval": "#E2B657",
    "approved": "#69D99A",
    "running": "#8FB0FF",
    "completed": "#69D99A",
    "failed": "#FF8585",
    "disabled": "#8B93A7",
    "archived": "#8B93A7",
    "rejected": "#FF8585",
    "skipped": "#E2B657",
    "retry": "#E2B657",
    "overlap": "#E2B657",
}


def status_text(value: str) -> str:
    labels = {
        "pending_approval": "Pending approval",
        "not_run": "Not run",
        "dry_run": "Dry run",
    }
    if value in labels:
        return labels[value]
    return str(value or "Unknown").replace("_", " ").capitalize()
