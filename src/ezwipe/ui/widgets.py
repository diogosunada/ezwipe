"""Building blocks for the EZWipe interface: drive cards, profile options, stats."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QFrame, QHBoxLayout, QLabel, QProgressBar, QPushButton,
                               QSizePolicy, QVBoxLayout, QWidget)

from ..core.methods import method_info
from ..core.models import Drive, MethodId, OperationState


def gb(n: int) -> str:
    if not n:
        return "?"
    return f"{n / 10**12:.2f} TB" if n >= 10**12 else f"{n / 10**9:,.0f} GB"


def repolish(w: QWidget) -> None:
    w.style().unpolish(w)
    w.style().polish(w)


def pill(text: str, kind: str) -> QLabel:
    lb = QLabel(text)
    lb.setProperty("pill", kind)
    return lb


def set_pill(lb: QLabel, text: str, kind: str) -> None:
    lb.setText(text)
    lb.setProperty("pill", kind)
    repolish(lb)


STATE_KIND = {
    OperationState.COMPLETED_VERIFIED: "ok",
    OperationState.COMPLETED_WITH_CAVEATS: "ok",
    OperationState.FAILED: "bad",
    OperationState.BLOCKED: "bad",
    OperationState.INTERRUPTED: "warn",
    OperationState.INCONCLUSIVE: "warn",
}

STATE_SHORT = {
    OperationState.COMPLETED_VERIFIED: "Erased · verified",
    OperationState.COMPLETED_WITH_CAVEATS: "Erased · verified with notes",
    OperationState.FAILED: "Failed",
    OperationState.BLOCKED: "Not started",
    OperationState.INTERRUPTED: "Stopped",
    OperationState.INCONCLUSIVE: "Not verified",
}


def ui_key(drive: Drive) -> str:
    """One key per physical slot, so drives that report the same serial
    (multi-bay enclosures) still get a card each. The engine and the audit log
    keep using the identity key (model|serial)."""
    return f"{drive.identity.key()}@{drive.device_path}"


def running_label(state: OperationState, progress: float | None) -> str:
    if state in (OperationState.WAITING, OperationState.READY):
        return "Queued"
    if state == OperationState.CHECKING:
        return "Checking drive"
    if state == OperationState.VERIFYING:
        return "Verifying"
    return f"Erasing {progress:.0%}" if progress is not None else "Erasing"


@dataclass
class CardStatus:
    """What a card shows for one drive under the selected profile."""
    method: MethodId | None = None
    allowed: bool = False
    blocker: str = ""
    op_id: str = ""
    op_state: OperationState | None = None
    message: str = ""
    progress: float | None = None
    certificate: str = ""


class StatTile(QFrame):
    def __init__(self, label: str):
        super().__init__()
        self.setObjectName("stat")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 10, 16, 10)
        lay.setSpacing(0)
        self.value = QLabel("0")
        self.value.setObjectName("statValue")
        cap = QLabel(label)
        cap.setObjectName("statLabel")
        lay.addWidget(self.value)
        lay.addWidget(cap)

    def set(self, n: int):
        self.value.setText(str(n))


class Banner(QFrame):
    def __init__(self, text: str, bad: bool = False, button: str | None = None):
        super().__init__()
        self.setObjectName("bannerBad" if bad else "banner")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 8, 10, 8)
        self.label = QLabel(text)
        self.label.setWordWrap(True)
        lay.addWidget(self.label, 1)
        self.button = None
        if button:
            self.button = QPushButton(button)
            self.button.setObjectName("primary")
            lay.addWidget(self.button)


class DriveCard(QFrame):
    toggled = Signal(str, bool)          # drive key, selected
    details = Signal(str)
    certificate = Signal(str)
    stop = Signal(str)

    def __init__(self, drive: Drive):
        super().__init__()
        self.drive = drive
        self.key = ui_key(drive)
        self.status = CardStatus()
        self.setObjectName("card")
        self.setMinimumWidth(330)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setCursor(Qt.PointingHandCursor)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(6)

        top = QHBoxLayout()
        self.check = QCheckBox()
        self.check.toggled.connect(lambda on: (self._mark(), self.toggled.emit(self.key, on)))
        kind = QLabel(drive.kind)
        kind.setObjectName("kind")
        self.pill = pill("", "ready")
        top.addWidget(self.check)
        top.addWidget(kind)
        top.addStretch(1)
        top.addWidget(self.pill)
        lay.addLayout(top)

        i = drive.identity
        title = QLabel(i.model or "Unknown drive")
        title.setObjectName("cardTitle")
        title.setWordWrap(True)
        lay.addWidget(title)
        serial = i.serial or "no serial"
        facts = [gb(i.capacity_bytes), f"SN {serial}"]
        sub = QLabel("  ·  ".join(facts))
        sub.setObjectName("cardSub")
        sub.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lay.addWidget(sub)

        health = []
        if drive.temperature_c is not None:
            health.append(f"{drive.temperature_c}°C")
        if drive.power_on_hours is not None:
            health.append(f"{drive.power_on_hours:,} h on")
        if drive.wear_percent is not None:
            health.append(f"{drive.wear_percent}% worn")
        health += drive.health_notes[:1]
        self.health = QLabel("  ·  ".join(health))
        self.health.setObjectName("cardNote")
        self.health.setVisible(bool(health))
        lay.addWidget(self.health)

        self.method = QLabel()
        self.method.setObjectName("cardMethod")
        self.method.setWordWrap(True)
        lay.addWidget(self.method)

        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setVisible(False)
        lay.addWidget(self.bar)

        self.note = QLabel()
        self.note.setObjectName("cardNote")
        self.note.setWordWrap(True)
        lay.addWidget(self.note)

        btns = QHBoxLayout()
        self.details_btn = QPushButton("Details")
        self.details_btn.setObjectName("small")
        self.details_btn.clicked.connect(lambda: self.details.emit(self.key))
        self.cert_btn = QPushButton("Certificate")
        self.cert_btn.setObjectName("small")
        self.cert_btn.clicked.connect(lambda: self.certificate.emit(self.key))
        self.stop_btn = QPushButton("Stop")
        self.stop_btn.setObjectName("small")
        self.stop_btn.setToolTip("Stops EZWipe following the drive. A firmware erase already running "
                                 "inside the drive cannot always be stopped; the result is then "
                                 "recorded as stopped.")
        self.stop_btn.clicked.connect(lambda: self.stop.emit(self.key))
        btns.addWidget(self.details_btn)
        btns.addStretch(1)
        btns.addWidget(self.stop_btn)
        btns.addWidget(self.cert_btn)
        lay.addLayout(btns)
        self.apply(CardStatus())

    # ------------------------------------------------------------------ state
    @property
    def selectable(self) -> bool:
        s = self.status
        running = s.op_state is not None and not s.op_state.is_final
        return s.allowed and not running and not self.drive.is_protected

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton and self.selectable:
            self.check.setChecked(not self.check.isChecked())
        super().mousePressEvent(e)

    def set_selected(self, on: bool):
        if on and not self.selectable:
            on = False
        self.check.blockSignals(True)
        self.check.setChecked(on)
        self.check.blockSignals(False)
        self._mark()

    def _mark(self):
        self.setProperty("selected", "true" if self.check.isChecked() else "false")
        repolish(self)

    def apply(self, s: CardStatus):
        self.status = s
        d = self.drive
        running = s.op_state is not None and not s.op_state.is_final
        final = s.op_state is not None and s.op_state.is_final
        state = ""
        if d.is_protected:
            set_pill(self.pill, "Protected", "protected")
            self.method.setText(d.protection_reason or "Protected")
            state = "protected"
        elif running:
            set_pill(self.pill, running_label(s.op_state, s.progress), "running")
            self.method.setText(method_info(s.method).label if s.method else "")
            state = "running"
        elif final:
            kind = STATE_KIND.get(s.op_state, "warn")
            set_pill(self.pill, STATE_SHORT.get(s.op_state, s.op_state.value), kind)
            self.method.setText(method_info(s.method).label if s.method else "")
            state = kind
        elif s.allowed and s.method:
            set_pill(self.pill, "Ready", "ready")
            info = method_info(s.method)
            self.method.setText(f"Will use: {info.label}  ·  {info.category.value}")
        else:
            set_pill(self.pill, "Not for this profile" if s.blocker else "No method", "warn")
            self.method.setText(s.blocker or "No method reachable")
        self.setProperty("state", state)
        self.bar.setVisible(running)
        if running:
            if s.progress is None:
                self.bar.setRange(0, 0)          # busy: the drive gives no progress
            else:
                self.bar.setRange(0, 1000)
                self.bar.setValue(int(s.progress * 1000))
        self.note.setText(s.message if (running or final) else
                          "; ".join(d.limitations[:2]) if not d.is_protected else "")
        self.note.setVisible(bool(self.note.text()))
        self.stop_btn.setVisible(running)
        self.cert_btn.setVisible(final and bool(s.certificate))
        self.check.setEnabled(self.selectable)
        if not self.selectable and self.check.isChecked():
            self.set_selected(False)
        self._mark()


class ProfileOption(QFrame):
    clicked = Signal(str)

    def __init__(self, profile):
        super().__init__()
        self.profile_id = profile.profile_id
        self.setObjectName("profile")
        self.setCursor(Qt.PointingHandCursor)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(2)
        row = QHBoxLayout()
        name = QLabel(profile.name)
        name.setObjectName("profileName")
        cat = QLabel(profile.required_category.value.upper())
        cat.setObjectName("cat")
        row.addWidget(name)
        row.addStretch(1)
        row.addWidget(cat)
        lay.addLayout(row)
        line = QLabel(profile.tagline or profile.description)
        line.setObjectName("profileLine")
        line.setWordWrap(True)
        lay.addWidget(line)
        self.setToolTip(profile.description)

    def set_checked(self, on: bool):
        self.setProperty("checked", "true" if on else "false")
        repolish(self)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.clicked.emit(self.profile_id)
        super().mousePressEvent(e)
