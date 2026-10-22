"""EZWipe interface (PySide6), shared by the boot USB and the Windows edition.

The GUI never sends commands to a drive. It asks the engine for a plan,
shows it, collects the operator's confirmation and passes it back.
Engine callbacks arrive on a worker thread and are forwarded to the GUI
thread through a Qt signal.

Layout: top bar (mode, machine, clock, menu) · banners · stats · drive
cards on the left · profile picker and the Erase button on the right ·
History tab with every certificate.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QApplication, QDialog, QFileDialog, QFrame, QGridLayout, QHBoxLayout,
                               QLabel, QMainWindow, QMenu, QMessageBox, QPushButton, QScrollArea,
                               QTabWidget, QVBoxLayout, QWidget)

from .. import APP_NAME, __version__
from ..audit.store import AuditStore
from ..config import Settings, data_dir, data_is_persistent, load_settings
from ..core.models import Drive, OperationState
from ..editions import Edition, build_edition
from ..engine.engine import Engine
from ..platform import sysinfo
from ..reports.export import history_csv
from .dialogs import AutoCountdownDialog, CertificateViewer, ConfirmDialog, DetailsDialog
from .history import HistoryPage
from .style import palette, stylesheet
from .widgets import Banner, CardStatus, DriveCard, ProfileOption, StatTile

CARD_MIN_W = 360


class EngineBridge(QObject):
    """Moves engine callbacks from the worker thread to the GUI thread."""

    update = Signal(str, str, str, object)   # op_id, state value, message, progress

    def listener(self, op_id, state, message, progress):
        self.update.emit(op_id, state.value, message, progress)


class MainWindow(QMainWindow):
    def __init__(self, engine: Engine, store: AuditStore, edition: Edition, settings: Settings,
                 data: Path, kiosk: bool = False, auto_profile: str | None = None):
        super().__init__()
        self.engine, self.store, self.edition, self.settings = engine, store, edition, settings
        self.simulated = edition.name == "simulated"
        self.data = data
        self.reports_dir = data / "reports"
        self.kiosk = kiosk
        self.dark = settings.dark_mode
        self.colors = palette(self.dark)
        self.profile_id = "auto-purge"
        self.cards: dict[str, DriveCard] = {}
        self.selected: set[str] = set()
        self.status: dict[str, CardStatus] = {}        # drive key -> live/last status
        self.op_to_key: dict[str, str] = {}
        self._pending: list[tuple] = []
        self.bridge = EngineBridge()
        self.bridge.update.connect(self._on_update)
        self.auto_profile = auto_profile or (settings.auto_profile or None)

        self.setWindowTitle(f"{APP_NAME} {__version__}")
        self.resize(1440, 900)
        root = QWidget()
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(self._top_bar())

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        outer.addWidget(self.tabs, 1)
        self.tabs.addTab(self._drives_page(), "Drives")
        self.history = HistoryPage(self)
        self.tabs.addTab(self.history, "History")
        self.tabs.currentChanged.connect(lambda i: i == 1 and self.history.load())

        self.clock_timer = QTimer(self)
        self.clock_timer.timeout.connect(self._tick_clock)
        self.clock_timer.start(1000)
        self._tick_clock()

        self.refresh()
        if self.auto_profile:
            QTimer.singleShot(400, self._auto_mode)

    # ================================================================ layout
    def _top_bar(self) -> QFrame:
        bar = QFrame()
        bar.setObjectName("topBar")
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(20, 10, 16, 10)
        brand = QLabel("EZWipe")
        brand.setObjectName("brand")
        dot = QLabel("●")
        dot.setObjectName("brandDot")
        lay.addWidget(brand)
        lay.addWidget(dot)
        mode = {"simulated": ("SIMULATION", "badgeSim"), "linux": ("BOOT USB", "badgeLive"),
                "windows": ("WINDOWS · READ-ONLY", "badgeLive")}.get(self.edition.name, ("LIVE", "badgeLive"))
        b = QLabel(mode[0])
        b.setObjectName(mode[1])
        lay.addWidget(b)
        if self.auto_profile:
            a = QLabel("AUTO MODE")
            a.setObjectName("badgeAuto")
            lay.addWidget(a)
        lay.addSpacing(16)
        sysd = self.engine.context.get("system") or {}
        m = QLabel(sysinfo.describe(sysd))
        m.setObjectName("machine")
        lay.addWidget(m)
        lay.addStretch(1)
        self.clock = QLabel()
        self.clock.setObjectName("clock")
        lay.addWidget(self.clock)
        lay.addSpacing(12)
        refresh = QPushButton("⟳  Rescan")
        refresh.clicked.connect(self.refresh)
        lay.addWidget(refresh)
        menu_btn = QPushButton("☰")
        menu_btn.setObjectName("icon")
        menu = QMenu(menu_btn)
        menu.addAction("Unfreeze drives (sleep and wake)", self.unfreeze)
        menu.addAction("Export history (CSV)", self.export_csv)
        menu.addAction("Where reports are saved", self.show_paths)
        menu.addAction("Switch light / dark", self.toggle_theme)
        menu.addSeparator()
        if self.edition.name == "linux":
            menu.addAction("Restart computer", lambda: self.power("reboot"))
            menu.addAction("Shut down computer", lambda: self.power("poweroff"))
        if not self.kiosk:
            menu.addAction("Quit EZWipe", self.close)
        menu_btn.setMenu(menu)
        lay.addWidget(menu_btn)
        return bar

    def _drives_page(self) -> QWidget:
        page = QWidget()
        h = QHBoxLayout(page)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)

        main = QWidget()
        v = QVBoxLayout(main)
        v.setContentsMargins(20, 16, 20, 16)
        v.setSpacing(12)
        self.banner_box = QVBoxLayout()
        self.banner_box.setSpacing(8)
        v.addLayout(self.banner_box)
        stats = QHBoxLayout()
        self.tiles = {}
        for k, label in [("connected", "Drives found"), ("ready", "Ready for this profile"),
                         ("protected", "Protected"), ("running", "Erasing now"),
                         ("done", "Erased"), ("review", "Need a look")]:
            t = StatTile(label)
            self.tiles[k] = t
            stats.addWidget(t)
        v.addLayout(stats)
        self.grid_host = QWidget()
        self.grid = QGridLayout(self.grid_host)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(14)
        self.grid.setAlignment(Qt.AlignTop)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.grid_host)
        v.addWidget(scroll, 1)
        h.addWidget(main, 1)
        h.addWidget(self._side_panel())
        return page

    def _side_panel(self) -> QFrame:
        side = QFrame()
        side.setObjectName("side")
        side.setFixedWidth(340)
        v = QVBoxLayout(side)
        v.setContentsMargins(18, 18, 18, 18)
        v.setSpacing(10)
        t = QLabel("ERASE PROFILE")
        t.setObjectName("sideTitle")
        v.addWidget(t)
        self.profile_opts: dict[str, ProfileOption] = {}
        main_ids = ["auto-purge", "nist-clear", "zero-fill", "dod-3pass"]
        for p in self.engine.policy.profiles():
            if p.profile_id not in main_ids:
                continue
            opt = ProfileOption(p)
            opt.clicked.connect(self.set_profile)
            self.profile_opts[p.profile_id] = opt
            v.addWidget(opt)
        more = QPushButton("More profiles…")
        more.setObjectName("link")
        menu = QMenu(more)
        for p in self.engine.policy.profiles():
            if p.profile_id not in main_ids:
                menu.addAction(p.name, lambda pid=p.profile_id: self.set_profile(pid))
        more.setMenu(menu)
        v.addWidget(more, 0, Qt.AlignLeft)
        self.profile_note = QLabel()
        self.profile_note.setObjectName("muted")
        self.profile_note.setWordWrap(True)
        v.addWidget(self.profile_note)
        v.addStretch(1)

        sel = QHBoxLayout()
        all_btn = QPushButton("Select all ready")
        all_btn.clicked.connect(self.select_all_ready)
        none_btn = QPushButton("Clear")
        none_btn.clicked.connect(lambda: self.set_selection(set()))
        sel.addWidget(all_btn, 1)
        sel.addWidget(none_btn)
        v.addLayout(sel)
        self.sel_label = QLabel()
        self.sel_label.setObjectName("selCount")
        v.addWidget(self.sel_label)
        self.erase_btn = QPushButton("Erase")
        self.erase_btn.setObjectName("danger")
        self.erase_btn.clicked.connect(self.start_erase)
        v.addWidget(self.erase_btn)
        return side

    # ================================================================ drives
    def refresh(self):
        if self._running_keys():
            QMessageBox.information(self, APP_NAME, "Rescan is available when no erase is running.")
            return
        try:
            self.engine.refresh()
        except Exception as e:      # discovery must never take the interface down
            QMessageBox.critical(self, APP_NAME, f"Drive discovery failed:\n{e}")
            return
        self._rebuild_cards()
        self._replan()
        self._update_banners()

    def _rebuild_cards(self):
        while self.grid.count():
            w = self.grid.takeAt(0).widget()
            if w:
                w.deleteLater()
        self.cards = {}
        drives = sorted(self.engine.drives, key=lambda d: (d.is_protected, d.kind, d.device_path))
        for d in drives:
            card = DriveCard(d)
            card.toggled.connect(self._on_toggle)
            card.details.connect(self.show_details)
            card.certificate.connect(self.show_certificate_for_key)
            card.stop.connect(self.stop_drive)
            self.cards[card.key] = card
        self.selected &= set(self.cards)
        self._layout_cards()

    def _layout_cards(self):
        cols = max(1, (self.grid_host.width() or 900) // CARD_MIN_W)
        cards = list(self.cards.values())
        for i, c in enumerate(cards):
            self.grid.addWidget(c, i // cols, i % cols, Qt.AlignTop)
        for c in range(cols):
            self.grid.setColumnStretch(c, 1)
        self._cols = cols

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self.cards and max(1, self.grid_host.width() // CARD_MIN_W) != getattr(self, "_cols", 0):
            for c in self.cards.values():
                self.grid.removeWidget(c)
            self._layout_cards()

    def set_profile(self, profile_id: str):
        self.profile_id = profile_id
        self._replan()

    def _replan(self):
        p = self.engine.policy.get(self.profile_id)
        for pid, opt in self.profile_opts.items():
            opt.set_checked(pid == self.profile_id)
        self.profile_note.setText(f"<b>{p.name}</b>: {p.description}")
        plan = self.engine.plan(self.engine.drives, self.profile_id)
        for item in plan.items:
            key = item.drive.identity.key()
            st = self.status.get(key) or CardStatus()
            if st.op_state is None or st.op_state.is_final:
                st.method = item.method if st.op_state is None else st.method
                st.allowed = item.allowed
                st.blocker = self._blocker_text(item, p)
            self.status[key] = st
            if key in self.cards:
                self.cards[key].apply(st)
        self.selected = {k for k in self.selected if k in self.cards and self.cards[k].selectable}
        self.set_selection(self.selected)

    def _blocker_text(self, item, profile) -> str:
        """Say why a drive can't run, and which profile would work instead."""
        if item.allowed or item.drive.is_protected:
            return "; ".join(item.blockers)
        if item.method is not None:
            return "; ".join(item.blockers)
        works = [self.engine.policy.get(pid).name for pid in self.profile_opts
                 if pid != profile.profile_id and self.engine.plan([item.drive], pid).items[0].allowed]
        text = f"No method for {profile.name} can run on this drive here."
        return text + (f" Works with: {', '.join(works)}." if works else "")

    def _on_toggle(self, key: str, on: bool):
        (self.selected.add if on else self.selected.discard)(key)
        self._update_side()

    def set_selection(self, keys: set[str]):
        self.selected = set(keys)
        for k, c in self.cards.items():
            c.set_selected(k in self.selected)
        self.selected = {k for k, c in self.cards.items() if c.check.isChecked()}
        self._update_side()

    def select_all_ready(self):
        self.set_selection({k for k, c in self.cards.items() if c.selectable})

    def _update_side(self):
        n = len(self.selected)
        size = sum(self.cards[k].drive.identity.capacity_bytes for k in self.selected)
        self.sel_label.setText(f"<b>{n}</b> drive{'s' if n != 1 else ''} selected"
                               + (f" · {size / 10**9:,.0f} GB" if n else ""))
        self.erase_btn.setText(f"Erase {n} drive{'s' if n != 1 else ''}" if n else "Select drives to erase")
        self.erase_btn.setEnabled(n > 0)
        self._update_tiles()

    def _running_keys(self) -> set[str]:
        return {k for k, s in self.status.items() if s.op_state is not None and not s.op_state.is_final}

    def _update_tiles(self):
        drives = self.engine.drives
        st = self.status.values()
        self.tiles["connected"].set(len(drives))
        self.tiles["ready"].set(sum(1 for c in self.cards.values() if c.selectable))
        self.tiles["protected"].set(sum(1 for d in drives if d.is_protected))
        self.tiles["running"].set(len(self._running_keys()))
        self.tiles["done"].set(sum(1 for s in st if s.op_state in (OperationState.COMPLETED_VERIFIED,
                                                                    OperationState.COMPLETED_WITH_CAVEATS)))
        self.tiles["review"].set(sum(1 for s in st if s.op_state in (
            OperationState.FAILED, OperationState.INCONCLUSIVE, OperationState.INTERRUPTED)))

    def _update_banners(self):
        while self.banner_box.count():
            w = self.banner_box.takeAt(0).widget()
            if w:
                w.deleteLater()
        for note in self.edition.notes:
            self.banner_box.addWidget(Banner(note))
        for err in self.settings.errors:
            self.banner_box.addWidget(Banner(err, bad=True))
        if not data_is_persistent(self.data):
            self.banner_box.addWidget(Banner(
                "Certificates are kept in memory only and will be lost at shutdown. Add an "
                "EZWIPE-DATA partition to the USB (see the guide) or export them before switching off.",
                bad=True))
        frozen = self.engine.frozen_drives()
        if frozen and self.engine.can_unfreeze():
            b = Banner(f"{len(frozen)} drive{'s are' if len(frozen) != 1 else ' is'} frozen by the BIOS, "
                       f"which blocks Secure Erase and Sanitize. Unfreeze puts the computer to sleep for a "
                       f"few seconds and wakes it again.", button="Unfreeze")
            b.button.clicked.connect(self.unfreeze)
            self.banner_box.addWidget(b)

    # ================================================================ erase
    def start_erase(self):
        drives = [self.cards[k].drive for k in self.selected if k in self.cards]
        if not drives:
            return
        profile = self.engine.policy.get(self.profile_id)
        plan = self.engine.plan(drives, self.profile_id)
        if not plan.runnable:
            QMessageBox.warning(self, APP_NAME, "None of the selected drives can be erased with this "
                                                "profile:\n\n" + "\n".join(
                f"- {i.drive.display_name}: {'; '.join(i.blockers)}" for i in plan.items))
            return
        dlg = ConfirmDialog(plan, profile.name, self)
        if dlg.exec() != QDialog.Accepted:
            return
        plan.items = plan.runnable
        self._launch(plan, dlg.confirmations())

    def _launch(self, plan, confirmations, auto_by: str | None = None):
        op_ids = self.engine.start(plan, confirmations, self.bridge.listener, auto_confirmed_by=auto_by)
        for op_id, it in zip(op_ids, plan.items):
            key = it.drive.identity.key()
            self.op_to_key[op_id] = key
            st = self.status.get(key) or CardStatus()
            st.op_id, st.method, st.op_state, st.message, st.progress, st.certificate = \
                op_id, it.method, OperationState.WAITING, "Queued", None, ""
            self.status[key] = st
            if key in self.cards:
                self.cards[key].apply(st)
        self.set_selection(set())
        for args in self._pending:
            self._on_update(*args)
        self._pending = []

    def _on_update(self, op_id: str, state: str, message: str, progress):
        key = self.op_to_key.get(op_id)
        if key is None:
            self._pending.append((op_id, state, message, progress))
            return
        st = self.status[key]
        st.op_state = OperationState(state)
        if message:
            st.message = message
        # Never invent a percentage (spec 6.3): only what the drive or EZWipe measured.
        st.progress = progress if progress is not None else (None if not st.op_state.is_final else st.progress)
        if st.op_state.is_final:
            st.progress = None
            op = self.store.operation(op_id)
            if op and op.get("verdict"):
                st.message = op["verdict"].get("summary", st.message)
            if st.op_state != OperationState.BLOCKED:
                st.certificate = self._make_certificate(op_id)
        if key in self.cards:
            self.cards[key].apply(st)
        self._update_tiles()
        if st.op_state.is_final and not self._running_keys():
            QTimer.singleShot(300, self._batch_done)

    def _make_certificate(self, op_id: str) -> str:
        from ..reports.certificate import write_certificate
        logo = self.data / self.settings.report_logo if self.settings.report_logo else None
        try:
            return str(write_certificate(self.store, op_id, self.reports_dir,
                                         logo_path=str(logo) if logo and logo.is_file() else None))
        except Exception as e:
            self.store.append_event("report.failed", {"error": repr(e)}, op_id)
            return ""

    def _batch_done(self):
        self._replan()
        if self.tabs.currentIndex() == 1:
            self.history.load()

    def stop_drive(self, key: str):
        st = self.status.get(key)
        if st and st.op_id:
            if QMessageBox.question(self, APP_NAME, "Stop this erase? The drive will be recorded as not "
                                                    "erased.") == QMessageBox.Yes:
                self.engine.cancel(st.op_id)

    # ================================================================ auto mode
    def _auto_mode(self):
        pid = self.auto_profile
        try:
            profile = self.engine.policy.get(pid)
        except KeyError:
            QMessageBox.warning(self, APP_NAME, f"Auto mode: unknown profile '{pid}'. Auto mode is off.")
            return
        self.set_profile(pid)
        plan = self.engine.plan_all(pid)
        if not plan.runnable:
            QMessageBox.information(self, APP_NAME, "Auto mode found no drive it can erase with "
                                                    f"'{profile.name}'.")
            return
        dlg = AutoCountdownDialog(plan, profile.name, self.settings.auto_delay_s, self)
        if dlg.exec() != QDialog.Accepted:
            self.store.append_event("auto.cancelled", {"profile": pid})
            return
        plan.items = plan.runnable
        by = (f"{self.settings.source} sha256={self.settings.sha256}" if self.settings.source
              else "--auto command-line flag")
        self._launch(plan, {}, auto_by=by)

    # ================================================================ other actions
    def unfreeze(self):
        if not self.engine.can_unfreeze():
            QMessageBox.information(self, APP_NAME, "Unfreeze is only available on the boot USB.")
            return
        if self._running_keys():
            QMessageBox.information(self, APP_NAME, "Wait until no erase is running.")
            return
        if QMessageBox.question(self, APP_NAME, "The computer will sleep for about 6 seconds and wake "
                                                "up by itself. Nothing is erased. Continue?") != QMessageBox.Yes:
            return
        msg = self.engine.unfreeze()
        self._rebuild_cards()
        self._replan()
        self._update_banners()
        QMessageBox.information(self, APP_NAME, msg)

    def show_details(self, key: str):
        d = self.cards[key].drive
        DetailsDialog(d, self.store.history_for(key), self.dark, self).exec()

    def store_certificates(self) -> set[str]:
        return {r["op_id"] for r in self.store.reports() if r["format"] == "pdf"}

    def show_certificate_for_key(self, key: str):
        st = self.status.get(key)
        if st and st.certificate:
            CertificateViewer(st.certificate, self).exec()

    def show_certificate_for_op(self, op_id: str):
        pdfs = [r["path"] for r in self.store.reports(op_id) if r["format"] == "pdf"]
        path = pdfs[-1] if pdfs and Path(pdfs[-1]).is_file() else self._make_certificate(op_id)
        if path:
            CertificateViewer(path, self).exec()

    def export_csv(self):
        default = str(self.reports_dir / f"ezwipe-history-{datetime.now():%Y%m%d-%H%M}.csv")
        path = default if self.kiosk else QFileDialog.getSaveFileName(
            self, "Export history", default, "CSV (*.csv)")[0]
        if path:
            Path(path).write_text(history_csv(self.store), encoding="utf-8")
            QMessageBox.information(self, APP_NAME, f"History saved:\n{path}")

    def show_paths(self):
        QMessageBox.information(self, APP_NAME,
                                f"Reports and certificates: {self.reports_dir}\n"
                                f"Audit database: {self.store.path}\n"
                                f"Settings file: {self.settings.source or (str(self.data / 'ezwipe.toml') + ' (not present)')}")

    def toggle_theme(self):
        self.dark = not self.dark
        self.colors = palette(self.dark)
        QApplication.instance().setStyleSheet(stylesheet(self.dark))

    def power(self, action: str):
        if self._running_keys():
            QMessageBox.warning(self, APP_NAME, "An erase is running. Stop it or wait before switching off.")
            return
        if QMessageBox.question(self, APP_NAME, f"{action.title()} the computer now?") == QMessageBox.Yes:
            self.store.append_event("app.power", {"action": action})
            subprocess.Popen(["systemctl", action])

    def _tick_clock(self):
        self.clock.setText(datetime.now().strftime("%H:%M"))

    def closeEvent(self, e):
        if self._running_keys():
            QMessageBox.warning(self, APP_NAME, "An erase is running. Stop it before quitting.")
            e.ignore()
            return
        super().closeEvent(e)


# ==================================================================== startup

def build(simulate: bool, db_path: Path | None = None, data: Path | None = None):
    data = data or data_dir()
    settings = load_settings(data)
    edition = build_edition(simulate, data_mount=str(data))
    store = AuditStore(db_path or (data / ("audit-sim.db" if edition.name == "simulated" else "audit.db")))
    context = {"system": sysinfo.collect(), "operator": settings.operator,
               "organization": settings.organization, "location": settings.location,
               "edition": edition.name}
    engine = Engine(edition.discovery, edition.backends, store,
                    operator_id=settings.operator or os.environ.get("USERNAME") or os.environ.get("USER"),
                    organization=settings.organization or None, context=context)
    return engine, store, edition, settings, data


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="ezwipe")
    ap.add_argument("--simulate", action="store_true", help="run with simulated drives")
    ap.add_argument("--kiosk", action="store_true", help="full screen, no quit (boot USB)")
    ap.add_argument("--auto", metavar="PROFILE", help="auto mode with this profile (overrides ezwipe.toml)")
    args = ap.parse_args(argv if argv is not None else sys.argv[1:])
    app = QApplication(sys.argv[:1])
    app.setApplicationName(APP_NAME)
    app.setFont(QFont("Noto Sans" if sys.platform.startswith("linux") else "Segoe UI", 10))
    engine, store, edition, settings, data = build(args.simulate)
    app.setStyleSheet(stylesheet(settings.dark_mode))
    w = MainWindow(engine, store, edition, settings, data, kiosk=args.kiosk, auto_profile=args.auto)
    if args.kiosk:
        w.setWindowFlag(Qt.FramelessWindowHint)
        w.showFullScreen()
    else:
        w.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
