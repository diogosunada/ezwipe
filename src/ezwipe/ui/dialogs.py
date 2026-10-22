"""Dialogs: erase confirmation, drive details, auto-mode countdown, certificate viewer."""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QCheckBox, QDialog, QFrame, QGridLayout, QHBoxLayout, QLabel,
                               QLineEdit, QPushButton, QScrollArea, QTextBrowser, QVBoxLayout,
                               QWidget)

from ..capability import detector
from ..core.methods import method_info
from ..core.models import Availability, Drive
from ..engine.engine import BatchPlan
from .style import palette
from .widgets import gb, repolish


def _frame(child_layout) -> QFrame:
    f = QFrame()
    f.setObjectName("card")
    f.setLayout(child_layout)
    return f


class ConfirmDialog(QDialog):
    """Explicit confirmation (spec 7.3): one code per drive + one acknowledgement."""

    def __init__(self, plan: BatchPlan, profile_name: str, parent=None):
        super().__init__(parent)
        self.plan = plan
        runnable = plan.runnable
        blocked = [i for i in plan.items if not i.allowed]
        self.setWindowTitle("Confirm erase")
        self.setMinimumSize(900, 620)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 24, 28, 24)
        lay.setSpacing(12)

        t = QLabel(f"Erase {len(runnable)} drive{'s' if len(runnable) != 1 else ''}?")
        t.setObjectName("dlgTitle")
        lay.addWidget(t)
        warn = QLabel(f"<b>All data on these drives will be destroyed and cannot be recovered.</b> "
                      f"Profile: {profile_name}. Check each serial number against the label on the drive, "
                      f"then type its last 4 characters.")
        warn.setObjectName("danger")
        warn.setWordWrap(True)
        lay.addWidget(warn)

        rows = QWidget()
        grid = QGridLayout(rows)
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(10)
        for c, h in enumerate(["Drive", "Serial", "Size", "Method", "Type last 4"]):
            hl = QLabel(h)
            hl.setObjectName("muted")
            grid.addWidget(hl, 0, c)
        self.inputs: dict[str, tuple[QLineEdit, str]] = {}
        for r, it in enumerate(runnable, 1):
            d = it.drive
            info = method_info(it.method)
            name = QLabel(f"<b>{d.kind}</b>  {d.identity.model}")
            name.setWordWrap(True)
            sn = d.identity.serial
            serial = QLabel(f"{sn[:-4]}<b><u>{sn[-4:]}</u></b>")
            serial.setTextInteractionFlags(Qt.TextSelectableByMouse)
            meth = QLabel(f"{info.label}<br><span style='color:gray'>{info.category.value}"
                          f"{' (provisional)' if info.category_provisional else ''}</span>")
            meth.setToolTip(f"{info.scope}\n\nVerification: {info.verification}\n\nWhy: {it.reason}")
            edit = QLineEdit()
            edit.setObjectName("code")
            edit.setMaxLength(4)
            edit.setFixedWidth(110)
            edit.setPlaceholderText("····")
            edit.textChanged.connect(self._validate)
            for c, w in enumerate([name, serial, QLabel(gb(d.identity.capacity_bytes)), meth, edit]):
                grid.addWidget(w, r, c)
            self.inputs[d.identity.key()] = (edit, it.confirmation_code)
        grid.setColumnStretch(0, 3)
        grid.setColumnStretch(3, 2)
        grid.setRowStretch(len(runnable) + 1, 1)      # rows stay at the top
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(rows)
        lay.addWidget(scroll, 1)

        if blocked:
            txt = "<b>Will not run:</b> " + "; ".join(
                f"{i.drive.identity.model} ({'; '.join(i.blockers)})" for i in blocked)
            bl = QLabel(txt)
            bl.setWordWrap(True)
            bl.setObjectName("muted")
            lay.addWidget(bl)

        lim = QLabel("EZWipe records the drive's own status and its read-back checks. They do not prove "
                     "that every physical block was erased; results without enough evidence are "
                     "recorded as not verified.")
        lim.setObjectName("muted")
        lim.setWordWrap(True)
        lay.addWidget(lim)
        self.ack = QCheckBox("I checked the serial numbers and understand the data cannot be recovered.")
        self.ack.toggled.connect(self._validate)
        lay.addWidget(self.ack)

        btns = QHBoxLayout()
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        self.go = QPushButton(f"Erase {len(runnable)} drive{'s' if len(runnable) != 1 else ''}")
        self.go.setObjectName("danger")
        self.go.setEnabled(False)
        self.go.clicked.connect(self.accept)
        btns.addWidget(cancel)
        btns.addStretch(1)
        btns.addWidget(self.go)
        lay.addLayout(btns)

    def _validate(self):
        ok_all = bool(self.inputs)
        for edit, code in self.inputs.values():
            ok = edit.text().strip().upper() == code
            if edit.property("ok") != ("true" if ok else "false"):
                edit.setProperty("ok", "true" if ok else "false")
                repolish(edit)
            ok_all &= ok
        self.go.setEnabled(ok_all and self.ack.isChecked())

    def confirmations(self) -> dict[str, str]:
        return {k: e.text() for k, (e, _) in self.inputs.items()}


class DetailsDialog(QDialog):
    def __init__(self, drive: Drive, history: list[dict], dark: bool, parent=None):
        super().__init__(parent)
        self.setWindowTitle(drive.identity.model)
        self.setMinimumSize(760, 640)
        c = palette(dark)
        i = drive.identity
        rec, why = detector.recommend(drive)

        def color(a):
            return {Availability.AVAILABLE: c["ok"], Availability.NOT_DECLARED: c["muted"]}.get(a, c["warn"])

        meth = "".join(
            f"<tr><td>{method_info(m.method).label}</td><td style='color:{color(m.availability)}'>"
            f"{m.availability.value}</td><td style='color:{c['muted']}'>{m.reason}</td></tr>"
            for m in drive.methods) or "<tr><td>No methods known for this drive.</td></tr>"
        lim = "".join(f"<li>{x}</li>" for x in drive.limitations) or "<li>None found</li>"
        hist = "".join(f"<li>{o['created_at'][:16].replace('T', ' ')} UTC · {o['method']} · "
                       f"<b>{o['state']}</b>{' (simulated)' if o['simulated'] else ''}</li>"
                       for o in history[:10]) or "<li>No earlier operations on this drive</li>"
        ata = drive.capabilities.get("ata") or {}
        nv = drive.capabilities.get("nvme") or {}
        raw = ""
        if nv:
            raw = (f"SANICAP 0x{nv.get('sanicap', 0):08X} · OACS 0x{nv.get('oacs', 0):04X} · "
                   f"FNA 0x{nv.get('fna', 0):02X} · last sanitize: {nv.get('last_sanitize') or '?'}")
        elif ata:
            raw = (f"Security {'enabled' if ata.get('security_enabled') else 'not enabled'}, "
                   f"{'frozen' if ata.get('frozen') else 'not frozen'}, "
                   f"{'locked' if ata.get('locked') else 'not locked'} · erase estimate "
                   f"{ata.get('enhanced_erase_minutes') or ata.get('erase_minutes') or '?'} min · "
                   f"HPA hidden {ata.get('hpa_hidden_sectors') or 0:,} sectors")
        b = QTextBrowser()
        b.setHtml(f"""
            <h2>{i.model}</h2>
            <table cellpadding=3>
              <tr><td>Serial</td><td><b>{i.serial or '?'}</b></td></tr>
              <tr><td>Capacity</td><td>{gb(i.capacity_bytes)} ({i.capacity_bytes:,} bytes)</td></tr>
              <tr><td>Firmware</td><td>{i.firmware or '?'}</td></tr>
              <tr><td>Interface</td><td>{drive.protocol.value} / {drive.bus_type.value} · {drive.connection.value}</td></tr>
              <tr><td>Device</td><td>{drive.device_path}</td></tr>
              <tr><td>Health</td><td>{drive.temperature_c if drive.temperature_c is not None else '?'} °C ·
                  {drive.power_on_hours if drive.power_on_hours is not None else '?'} power-on hours ·
                  wear {drive.wear_percent if drive.wear_percent is not None else '?'}%
                  {'<br>' + '<br>'.join(drive.health_notes) if drive.health_notes else ''}</td></tr>
            </table>
            <h3>Drive reports</h3><p>{raw or 'Not read'}</p>
            <h3>Methods</h3><table cellpadding=4>{meth}</table>
            <h3>Recommended</h3><p>{('<b>' + method_info(rec).label + '</b><br>' + why) if rec else why}</p>
            <h3>Limitations</h3><ul>{lim}</ul>
            <h3>History</h3><ul>{hist}</ul>""")
        lay = QVBoxLayout(self)
        lay.addWidget(b)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        lay.addWidget(close, 0, Qt.AlignRight)


class AutoCountdownDialog(QDialog):
    """Auto mode: show what will be erased and count down; any button stops it."""

    def __init__(self, plan: BatchPlan, profile_name: str, seconds: int, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Auto mode")
        self.setMinimumSize(820, 560)
        self.left = seconds
        runnable = plan.runnable
        lay = QVBoxLayout(self)
        lay.setContentsMargins(32, 28, 32, 28)
        t = QLabel("Auto mode will erase these drives")
        t.setObjectName("dlgTitle")
        lay.addWidget(t)
        sub = QLabel(f"Profile: <b>{profile_name}</b>. Set in ezwipe.toml. Press Cancel to stop "
                     f"and choose drives by hand.")
        sub.setWordWrap(True)
        lay.addWidget(sub)
        self.count = QLabel(str(seconds))
        self.count.setObjectName("countdown")
        self.count.setAlignment(Qt.AlignCenter)
        lay.addWidget(self.count)
        body = "".join(f"<li><b>{i.drive.identity.model}</b> · SN {i.drive.identity.serial} · "
                       f"{gb(i.drive.identity.capacity_bytes)} · {method_info(i.method).label}</li>"
                       for i in runnable)
        skip = "".join(f"<li>{i.drive.identity.model}: {'; '.join(i.blockers)}</li>"
                       for i in plan.items if not i.allowed)
        lst = QTextBrowser()
        lst.setHtml(f"<ul>{body}</ul>" + (f"<p><b>Skipped</b></p><ul>{skip}</ul>" if skip else ""))
        lay.addWidget(lst, 1)
        btns = QHBoxLayout()
        cancel = QPushButton("Cancel auto mode")
        cancel.setObjectName("primary")
        cancel.clicked.connect(self.reject)
        now = QPushButton("Start now")
        now.setObjectName("danger")
        now.clicked.connect(self.accept)
        btns.addWidget(cancel)
        btns.addStretch(1)
        btns.addWidget(now)
        lay.addLayout(btns)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(1000)

    def _tick(self):
        self.left -= 1
        self.count.setText(str(max(0, self.left)))
        if self.left <= 0:
            self.timer.stop()
            self.accept()


class CertificateViewer(QDialog):
    """Shows a PDF inside EZWipe (the boot USB has no PDF reader)."""

    def __init__(self, path: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Certificate")
        self.setMinimumSize(820, 900)
        lay = QVBoxLayout(self)
        try:
            from PySide6.QtPdf import QPdfDocument
            from PySide6.QtPdfWidgets import QPdfView
            self.doc = QPdfDocument(self)
            self.doc.load(path)
            view = QPdfView(self)
            view.setDocument(self.doc)
            view.setPageMode(QPdfView.PageMode.MultiPage)
            view.setZoomMode(QPdfView.ZoomMode.FitToWidth)
            lay.addWidget(view, 1)
        except ImportError:
            lay.addWidget(QLabel(f"Saved: {path}"))
        foot = QHBoxLayout()
        where = QLabel(path)
        where.setObjectName("muted")
        where.setTextInteractionFlags(Qt.TextSelectableByMouse)
        foot.addWidget(where, 1)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        foot.addWidget(close)
        lay.addLayout(foot)
