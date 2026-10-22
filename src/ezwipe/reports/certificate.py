"""PDF certificate of sanitization (spec 9).

Built only from the audit store, like the JSON record, and tied to it: the
certificate prints the SHA-256 of the JSON record written next to it, and
the PDF's own SHA-256 is recorded in the audit store.

Rendering uses Qt (QTextDocument + QPdfWriter), so no extra dependency.
A QGuiApplication must exist; the GUI has one and tests run offscreen.
"""

from __future__ import annotations

import hashlib
import html
from datetime import datetime, timezone
from pathlib import Path

from .. import APP_NAME, __version__
from ..audit.store import AuditStore
from .export import RESULT_WORDING, operation_report, write_json_report_with_digest

STATE_TINT = {
    "Completed and verified": ("#e7f6ee", "#1d7a4c"),
    "Completed with caveats": ("#eef6e7", "#3d7a1d"),
    "Inconclusive: sanitization not verified": ("#fdf3e1", "#93650d"),
    "Interrupted": ("#fdf3e1", "#93650d"),
    "Failed": ("#fbe9e8", "#a3302a"),
    "Blocked before execution": ("#fbe9e8", "#a3302a"),
}


def _e(x) -> str:
    return html.escape("" if x is None else str(x))


def _ts(s: str | None) -> str:
    if not s:
        return "-"
    try:
        return datetime.fromisoformat(s).strftime("%d %b %Y, %H:%M:%S UTC")
    except ValueError:
        return s


def _dur(a: str | None, b: str | None) -> str:
    try:
        s = int((datetime.fromisoformat(b) - datetime.fromisoformat(a)).total_seconds())
    except (TypeError, ValueError):
        return "-"
    return f"{s // 3600} h {s % 3600 // 60:02d} min" if s >= 3600 else f"{s // 60} min {s % 60:02d} s"


def _gb(n) -> str:
    try:
        return f"{int(n) / 10**9:,.1f} GB ({int(n):,} bytes)"
    except (TypeError, ValueError):
        return "-"


def _rows(pairs) -> str:
    return "".join(f"<tr><td class='k' width='32%'>{_e(k)}</td><td>{v}</td></tr>" for k, v in pairs if v not in (None, ""))


def certificate_html(rep: dict, json_sha256: str, fields: dict[str, str] | None = None,
                     logo_path: str | None = None) -> str:
    s = rep["sanitization"]
    v = s.get("verdict") or {}
    dev = rep["media"]
    ctx = rep.get("context") or {}
    sysi = ctx.get("system") or {}
    opts = ctx.get("options") or {}
    params = v.get("parameters") or {}
    state = s["state"]
    bg, fg = STATE_TINT.get(state, ("#eeeeee", "#333333"))
    checks = v.get("checks") or []
    mark = {True: "&#10003; passed", False: "&#10007; failed", None: "? could not decide"}
    check_rows = "".join(
        f"<tr><td class='k' width='32%'>{_e(c.get('check'))}</td><td><b>{mark.get(c.get('passed'))}</b><br>"
        f"<span class='q'>{_e(c.get('shows'))}</span></td></tr>" for c in checks) \
        or "<tr><td colspan=2 class='q'>No method checks were run.</td></tr>"
    caveats = "".join(f"<li>{_e(c)}</li>" for c in (v.get("caveats") or []))
    hpa = params.get("hpa") or {}
    hpa_text = ("Opened before erase" if hpa.get("opened") else
                "Present, could not be opened" if hpa else "None found / not applicable")
    vf = opts.get("verify_fraction")
    fields = {**(ctx.get("fields") or {}), **(fields or {})}
    op_rows = _rows([("Operator", _e(rep["audit"].get("operator") or ctx.get("operator"))),
                     ("Organization", _e(rep["audit"].get("organization") or ctx.get("organization"))),
                     ("Location", _e(ctx.get("location"))),
                     ("Confirmation", _e(ctx.get("confirmation")))]
                    + [(k, _e(val)) for k, val in fields.items()])
    logo = f"<img src='{_e(logo_path)}' height='48'>" if logo_path else ""
    sim = ("<p class='sim'>SIMULATED OPERATION: no real drive was sanitized. "
           "Not valid as a certificate.</p>") if rep.get("simulated") else ""
    ram = sysi.get("ram_bytes")
    return f"""
<html><head><style>
body {{ font-family: 'Noto Sans', 'Segoe UI', sans-serif; font-size: 9.5pt; color: #1f2328; }}
h1 {{ font-size: 18pt; margin: 0; }}
h2 {{ font-size: 11pt; margin: 14px 0 4px 0; color: #1f2328; border-bottom: 1px solid #d0d7de; }}
td {{ padding: 3px 6px; vertical-align: top; }}
td.k {{ color: #57606a; width: 34%; }}
.q {{ color: #57606a; }}
.sim {{ color: #a3302a; font-weight: bold; font-size: 11pt; }}
</style></head><body>
<table width='100%'><tr>
 <td><h1>{APP_NAME}</h1><span class='q'>Certificate of Data Sanitization</span></td>
 <td align='right'>{logo}<br><b>Certificate {_e(rep['operation_id'])}</b><br>
   <span class='q'>Batch {_e(rep.get('batch_id'))}</span></td>
</tr></table>
{sim}
<table width='100%' cellpadding='10' style='background:{bg};'><tr><td>
 <span style='font-size:15pt; font-weight:bold; color:{fg};'>{_e(s['result_statement'])}</span><br>
 <span>{_e(v.get('summary'))}</span>
</td></tr></table>

<h2>Storage device</h2>
<table width='100%'>{_rows([
    ("Model", _e(dev.get('model'))), ("Serial number", f"<b>{_e(dev.get('serial'))}</b>"),
    ("Capacity", _gb(dev.get('capacity_bytes'))), ("Firmware", _e(dev.get('firmware'))),
    ("Interface", _e(f"{dev.get('protocol')} / {dev.get('bus_type')}")),
    ("Connection", _e(dev.get('connection'))), ("Device path", _e(dev.get('device_path'))),
    ("NVMe EUI-64", _e(dev.get('eui64')))])}</table>

<h2>Sanitization</h2>
<table width='100%'>{_rows([
    ("Method", f"<b>{_e(v.get('method_name') or s['method'])}</b>"),
    ("NIST SP 800-88 category", _e(v.get('nist_category')) + " <span class='q'>(provisional, "
     "pending IEEE 2883 review)</span>" if v.get('nist_category') else ""),
    ("Profile", _e(ctx.get('profile_name') or s.get('profile'))), ("Policy version", _e(s.get('policy_version'))),
    ("Route", _e(v.get('route'))), ("Device status", _e(v.get('device_status'))),
    ("Hidden area (HPA)", _e(hpa_text) if dev.get('protocol') == 'ATA' else ""),
    ("Read-back", f"{vf:.0%} of the drive" if isinstance(vf, (int, float)) and 'host' in str(v.get('route'))
     else ""),
    ("Started", _ts(s.get('started_at'))), ("Completed", _ts(s.get('ended_at'))),
    ("Duration", _dur(s.get('started_at'), s.get('ended_at'))),
    ("Evidence level", _e(v.get('evidence_level')))])}</table>

<h2>Verification</h2>
<table width='100%'>{check_rows}</table>
{f"<p class='q'><b>Caveats</b></p><ul>{caveats}</ul>" if caveats else ""}

<h2>Host system</h2>
<table width='100%'>{_rows([
    ("System", _e(' '.join(x for x in (sysi.get('vendor'), sysi.get('model')) if x))),
    ("System serial", _e(sysi.get('serial'))), ("CPU", _e(sysi.get('cpu'))),
    ("Memory", f"{int(ram) / 2**30:.0f} GB" if ram else ""), ("Firmware", _e(sysi.get('firmware_mode'))),
    ("Software", _e(f"{rep['software']['name']} {rep['software']['version']}"))])}</table>

<h2>Operator</h2>
<table width='100%'>{op_rows}
<tr><td class='k' width='32%'>Signature</td><td><br>______________________________</td></tr></table>

<h2>Integrity</h2>
<table width='100%'>{_rows([
    ("Audit chain", _e(rep['audit'].get('chain_check'))),
    ("JSON record SHA-256", f"<span style='font-family:monospace;'>{_e(json_sha256)}</span>"),
    ("Generated", _ts(datetime.now(timezone.utc).isoformat()) + " by "
     + _e(f"{APP_NAME} {__version__}"))])}</table>
<p class='q'>{_e(rep.get('notice'))} Verification covers the checks listed above; sampled reads
do not prove that every physical block was erased.</p>
</body></html>"""


def write_certificate(store: AuditStore, op_id: str, out_dir: str | Path,
                      fields: dict[str, str] | None = None, logo_path: str | None = None) -> Path:
    from PySide6.QtCore import QMarginsF, QSizeF
    from PySide6.QtGui import QPageLayout, QPageSize, QPdfWriter, QTextDocument

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    _, json_sha = write_json_report_with_digest(store, op_id, out_dir)
    rep = operation_report(store, op_id)
    doc = QTextDocument()
    doc.setHtml(certificate_html(rep, json_sha, fields, logo_path))
    dev = rep["media"]
    safe = "".join(ch for ch in f"{dev.get('model', '')}_{dev.get('serial', '')}" if ch.isalnum() or ch in "-_")
    path = out_dir / f"{op_id}_{safe[:60]}.pdf"
    w = QPdfWriter(str(path))
    w.setPageLayout(QPageLayout(QPageSize(QPageSize.A4), QPageLayout.Portrait, QMarginsF(14, 14, 14, 14),
                                QPageLayout.Millimeter))
    w.setResolution(150)
    w.setTitle(f"{APP_NAME} certificate {op_id}")
    w.setCreator(f"{APP_NAME} {__version__}")
    doc.setPageSize(QSizeF(w.width(), w.height()))
    doc.print_(w)
    del w
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    store.record_report(op_id, "pdf", digest, str(path))
    return path
