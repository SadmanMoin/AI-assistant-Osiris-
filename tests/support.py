"""Shared helpers for Osiris tests."""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from osiris.app import OsirisApp  # noqa: E402


def make_app() -> tuple[OsirisApp, Path]:
    folder = Path(tempfile.mkdtemp(prefix="osiris-test-"))
    app = OsirisApp(folder)
    return app, folder


def cleanup(folder: Path) -> None:
    shutil.rmtree(folder, ignore_errors=True)


SAFE_SCRIPT = '''
def run(tools, params):
    tools.log.info("hello")
    return {"summary": "Library test finished."}
'''.strip() + "\n"
