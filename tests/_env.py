"""One test environment for the whole suite.

Every test module must import this **before** any server module, because `deck_preview`, `paths` and
`deck_tools` read these variables at call time. Modules that each set their own directories make the
suite order-dependent (a module imported later would silently win), which is exactly how
`PPT_UPLOAD_DIR` was found being captured too early once already.

The tree is removed at interpreter exit, not per module: the directories are shared.
"""
from __future__ import annotations

import atexit
import os
import shutil
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.mkdtemp(prefix="deck-tests-"))

os.environ["PPT_TEMPLATE_PATH"] = f"{ROOT / 'templates'}{os.pathsep}{ROOT / 'templates'}"
os.environ["PPT_OUTPUT_DIR"] = str(TMP / "out")
os.environ["PPT_UPLOAD_DIR"] = str(TMP / "uploads")
os.environ["PPT_PUBLIC_BASE"] = "http://test.local:8931"
# Decks built by tests live in per-test temp dirs under TMP; anything outside is refused by design.
os.environ["PPT_ALLOWED_ROOTS"] = str(TMP)
os.environ.setdefault("PPT_RENDER_CACHE", "1")

(TMP / "out").mkdir(parents=True, exist_ok=True)
(TMP / "uploads" / "user").mkdir(parents=True, exist_ok=True)

atexit.register(shutil.rmtree, TMP, True)
