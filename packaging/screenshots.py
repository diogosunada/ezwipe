"""Render interface screenshots and a sample certificate with simulated drives.

    python packaging/screenshots.py OUTPUT_DIR

Used for docs and for checking the layout without a display (offscreen).
"""

import os
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PySide6.QtCore import QSize  # noqa: E402
from PySide6.QtGui import QFont, QImage, QPainter  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from ezwipe.ui.app import MainWindow, build  # noqa: E402
from ezwipe.ui.style import stylesheet  # noqa: E402


def pump(app, engine, seconds=15):
    end = time.time() + seconds
    while time.time() < end and engine._active:
        app.processEvents()
        time.sleep(0.01)
    for _ in range(40):
        app.processEvents()
        time.sleep(0.01)


def main(out: str) -> None:
    out_dir = Path(out)
    out_dir.mkdir(parents=True, exist_ok=True)
    app = QApplication([])
    app.setFont(QFont("Segoe UI", 10))
    tmp = Path(tempfile.mkdtemp())
    engine, store, edition, settings, data = build(True, tmp / "a.db", data=tmp)
    app.setStyleSheet(stylesheet(True))
    lab = engine.backends[0].lab
    lab.step_delay = 0.002
    w = MainWindow(engine, store, edition, settings, data)
    w.resize(1500, 940)
    w.show()
    app.processEvents()
    w._layout_cards()
    app.processEvents()

    # Finish two drives, leave one running, select two.
    keys = {c.drive.identity.serial: k for k, c in w.cards.items()}
    plan = engine.plan([w.cards[keys["SIM-D4-0001"]].drive, w.cards[keys["SIM-D4-0004"]].drive], "auto-purge")
    w._launch(plan, {i.drive.identity.key(): i.confirmation_code for i in plan.items})
    pump(app, engine)
    lab.step_delay = 0.5
    lab.steps = 100
    plan = engine.plan([w.cards[keys["SIM-HDD-0005"]].drive], "auto-purge")
    w._launch(plan, {plan.items[0].drive.identity.key(): plan.items[0].confirmation_code})
    end = time.time() + 2.5
    while time.time() < end:
        app.processEvents()
        time.sleep(0.02)
    w.set_selection({keys["SIM-D4-0002"], keys["SIM-D4-0003"]})
    app.processEvents()
    w.grab().save(str(out_dir / "main-dark.png"))

    app.setStyleSheet(stylesheet(False))
    w.toggle_theme()
    app.processEvents()
    w.grab().save(str(out_dir / "main-light.png"))
    app.setStyleSheet(stylesheet(True))
    w.toggle_theme()

    # Confirmation dialog
    from ezwipe.ui.dialogs import ConfirmDialog
    plan = engine.plan([w.cards[keys["SIM-D4-0002"]].drive, w.cards[keys["SIM-D4-0003"]].drive], "auto-purge")
    dlg = ConfirmDialog(plan, "Best Purge", w)
    dlg.resize(980, 640)
    dlg.show()
    first = next(iter(dlg.inputs.values()))
    first[0].setText(first[1])
    app.processEvents()
    dlg.grab().save(str(out_dir / "confirm.png"))
    dlg.close()

    # Certificate page 1 as an image
    cert = w.status[keys["SIM-D4-0001"]].certificate
    from PySide6.QtPdf import QPdfDocument
    doc = QPdfDocument()
    doc.load(cert)
    img: QImage = doc.render(0, QSize(1240, 1754))
    img.save(str(out_dir / "certificate.png"))
    print("certificate:", cert)
    engine.cancel(w.status[keys["SIM-HDD-0005"]].op_id)
    engine.wait(10)
    store.close()


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "screenshots")
