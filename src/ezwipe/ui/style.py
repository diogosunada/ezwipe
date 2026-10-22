"""EZWipe visual style.

One calm surface, one blue accent for "working", green only for verified
results, amber for "needs a look", red only for destruction and failure.
Sizes are large enough to use on a bench touchscreen.
"""

from __future__ import annotations

DARK = {
    "bg": "#0f1216", "surface": "#171b21", "surface2": "#1e242c", "border": "#2a323c",
    "text": "#e8eaed", "muted": "#8d96a0", "accent": "#4c9be8", "accent_soft": "#16314d",
    "ok": "#3fb984", "ok_soft": "#123526", "warn": "#e3a53b", "warn_soft": "#3a2c10",
    "bad": "#ef5b52", "bad_soft": "#3d1614", "danger_btn": "#c2362e", "selected": "#4c9be8",
}
LIGHT = {
    "bg": "#f3f5f8", "surface": "#ffffff", "surface2": "#eef1f5", "border": "#d5dbe2",
    "text": "#1b1f24", "muted": "#5c6670", "accent": "#1f6fc5", "accent_soft": "#dceafa",
    "ok": "#1d8a57", "ok_soft": "#dcf3e7", "warn": "#a86d00", "warn_soft": "#fbefd6",
    "bad": "#c4312a", "bad_soft": "#fbe2e0", "danger_btn": "#c4312a", "selected": "#1f6fc5",
}

# Kept for callers that only need a colour per state.
STATE_COLORS = {"ok": DARK["ok"], "warn": DARK["warn"], "blocked": DARK["bad"],
                "running": DARK["accent"], "muted": DARK["muted"]}


def palette(dark: bool) -> dict:
    return DARK if dark else LIGHT


def stylesheet(dark: bool = True) -> str:
    c = palette(dark)
    return f"""
* {{ font-size: 14px; }}
QWidget {{ background: {c['bg']}; color: {c['text']}; }}
QToolTip {{ background: {c['surface2']}; color: {c['text']}; border: 1px solid {c['border']}; padding: 6px; }}

QFrame#topBar {{ background: {c['surface']}; border-bottom: 1px solid {c['border']}; }}
QLabel#brand {{ font-size: 22px; font-weight: 800; letter-spacing: 0.5px; background: transparent; }}
QLabel#brandDot {{ color: {c['danger_btn']}; font-size: 22px; font-weight: 800; background: transparent; }}
QLabel#machine, QLabel#clock {{ color: {c['muted']}; background: transparent; }}
QLabel#clock {{ font-size: 16px; font-weight: 600; color: {c['text']}; }}

QLabel#badgeLive {{ background: {c['accent_soft']}; color: {c['accent']}; border-radius: 10px; padding: 3px 10px; font-weight: 700; }}
QLabel#badgeSim  {{ background: {c['warn_soft']}; color: {c['warn']}; border-radius: 10px; padding: 3px 10px; font-weight: 700; }}
QLabel#badgeAuto {{ background: {c['bad_soft']}; color: {c['bad']}; border-radius: 10px; padding: 3px 10px; font-weight: 700; }}

QFrame#banner {{ background: {c['warn_soft']}; border: 1px solid {c['warn']}; border-radius: 10px; }}
QFrame#banner QLabel {{ background: transparent; color: {c['text']}; }}
QFrame#bannerBad {{ background: {c['bad_soft']}; border: 1px solid {c['bad']}; border-radius: 10px; }}
QFrame#bannerBad QLabel {{ background: transparent; color: {c['text']}; }}

QFrame#stat {{ background: {c['surface']}; border: 1px solid {c['border']}; border-radius: 12px; }}
QLabel#statValue {{ font-size: 26px; font-weight: 800; background: transparent; }}
QLabel#statLabel {{ color: {c['muted']}; background: transparent; font-size: 12px; }}

QFrame#card {{ background: {c['surface']}; border: 1px solid {c['border']}; border-radius: 14px; }}
QFrame#card[selected="true"] {{ border: 2px solid {c['selected']}; background: {c['accent_soft']}; }}
QFrame#card[state="running"] {{ border: 2px solid {c['accent']}; }}
QFrame#card[state="ok"] {{ border: 2px solid {c['ok']}; }}
QFrame#card[state="bad"] {{ border: 2px solid {c['bad']}; }}
QFrame#card[state="warn"] {{ border: 2px solid {c['warn']}; }}
QFrame#card[state="protected"] {{ background: {c['surface2']}; }}
QFrame#card QLabel {{ background: transparent; }}
QLabel#cardTitle {{ font-size: 16px; font-weight: 700; }}
QLabel#cardSub {{ color: {c['muted']}; }}
QLabel#cardMethod {{ font-weight: 600; }}
QLabel#cardNote {{ color: {c['muted']}; font-size: 12px; }}
QLabel#kind {{ background: {c['surface2']}; border: 1px solid {c['border']}; border-radius: 8px;
               padding: 2px 8px; font-weight: 800; font-size: 12px; }}

QLabel[pill="ready"]     {{ background: {c['ok_soft']}; color: {c['ok']}; border-radius: 9px; padding: 2px 10px; font-weight: 700; }}
QLabel[pill="running"]   {{ background: {c['accent_soft']}; color: {c['accent']}; border-radius: 9px; padding: 2px 10px; font-weight: 700; }}
QLabel[pill="ok"]        {{ background: {c['ok_soft']}; color: {c['ok']}; border-radius: 9px; padding: 2px 10px; font-weight: 700; }}
QLabel[pill="warn"]      {{ background: {c['warn_soft']}; color: {c['warn']}; border-radius: 9px; padding: 2px 10px; font-weight: 700; }}
QLabel[pill="bad"]       {{ background: {c['bad_soft']}; color: {c['bad']}; border-radius: 9px; padding: 2px 10px; font-weight: 700; }}
QLabel[pill="protected"] {{ background: {c['surface2']}; color: {c['muted']}; border-radius: 9px; padding: 2px 10px; font-weight: 700; }}

QProgressBar {{ background: {c['surface2']}; border: none; border-radius: 6px; height: 12px; text-align: center; color: transparent; }}
QProgressBar::chunk {{ background: {c['accent']}; border-radius: 6px; }}

QFrame#side {{ background: {c['surface']}; border-left: 1px solid {c['border']}; }}
QFrame#side QLabel, QFrame#side QCheckBox {{ background: transparent; }}
QLabel#sideTitle {{ font-size: 13px; font-weight: 700; color: {c['muted']}; letter-spacing: 1px; background: transparent; }}
QFrame#profile {{ background: {c['surface2']}; border: 1px solid {c['border']}; border-radius: 12px; }}
QFrame#profile[checked="true"] {{ border: 2px solid {c['accent']}; background: {c['accent_soft']}; }}
QFrame#profile QLabel {{ background: transparent; }}
QLabel#profileName {{ font-weight: 700; font-size: 15px; }}
QLabel#profileLine {{ color: {c['muted']}; font-size: 12px; }}
QLabel#cat {{ border: 1px solid {c['border']}; border-radius: 8px; padding: 1px 7px; font-size: 11px; font-weight: 700; color: {c['muted']}; }}
QLabel#selCount {{ font-size: 15px; background: transparent; }}

QPushButton {{ background: {c['surface2']}; border: 1px solid {c['border']}; border-radius: 10px; padding: 9px 16px; font-weight: 600; }}
QPushButton:hover {{ border-color: {c['accent']}; }}
QPushButton:disabled {{ color: {c['muted']}; }}
QPushButton#primary {{ background: {c['accent']}; border-color: {c['accent']}; color: #ffffff; }}
QPushButton#danger {{ background: {c['danger_btn']}; border-color: {c['danger_btn']}; color: #ffffff; font-size: 17px; padding: 14px; }}
QPushButton#danger:disabled {{ background: {c['bad_soft']}; border-color: {c['bad_soft']}; color: {c['muted']}; }}
QPushButton#link {{ background: transparent; border: none; color: {c['accent']}; padding: 4px 6px; }}
QPushButton#small {{ padding: 5px 10px; font-size: 12px; border-radius: 8px; }}
QPushButton#icon {{ padding: 6px 12px; font-size: 18px; }}

QTabBar::tab {{ background: transparent; color: {c['muted']}; padding: 10px 18px; font-weight: 700; border-bottom: 3px solid transparent; }}
QTabBar::tab:selected {{ color: {c['text']}; border-bottom: 3px solid {c['accent']}; }}
QTabWidget::pane {{ border: none; }}

QTableWidget {{ background: {c['surface']}; gridline-color: {c['border']}; border: 1px solid {c['border']}; border-radius: 10px;
               selection-background-color: {c['accent_soft']}; selection-color: {c['text']}; }}
QHeaderView::section {{ background: {c['surface2']}; color: {c['muted']}; border: none; border-bottom: 1px solid {c['border']}; padding: 8px; font-weight: 700; }}
QLineEdit, QComboBox {{ background: {c['surface2']}; border: 1px solid {c['border']}; border-radius: 8px; padding: 7px 10px; }}
QLineEdit:focus {{ border-color: {c['accent']}; }}
QLineEdit#code {{ font-family: monospace; font-size: 18px; font-weight: 700; letter-spacing: 4px; }}
QLineEdit#code[ok="true"] {{ border: 2px solid {c['ok']}; }}
QCheckBox {{ spacing: 10px; background: transparent; }}
QCheckBox::indicator {{ width: 20px; height: 20px; border: 2px solid {c['muted']}; border-radius: 5px; background: {c['surface2']}; }}
QCheckBox::indicator:checked {{ background: {c['accent']}; border-color: {c['accent']}; }}
QScrollArea {{ border: none; }}
QScrollBar:vertical {{ background: transparent; width: 10px; }}
QScrollBar::handle:vertical {{ background: {c['border']}; border-radius: 5px; min-height: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
QTextBrowser {{ background: {c['surface']}; border: 1px solid {c['border']}; border-radius: 10px; padding: 10px; }}
QDialog {{ background: {c['bg']}; }}
QLabel#dlgTitle {{ font-size: 22px; font-weight: 800; }}
QLabel#danger {{ background: {c['bad_soft']}; color: {c['text']}; border: 1px solid {c['bad']}; border-radius: 10px; padding: 12px; }}
QLabel#countdown {{ font-size: 64px; font-weight: 800; color: {c['bad']}; }}
QLabel#muted {{ color: {c['muted']}; }}
"""


# Backwards-compatible name.
STYLESHEET = stylesheet(True)
