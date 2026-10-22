"""Report export (Phase 1: JSON and CSV; PDF certificates come in Phase 4).

A report is built only from the audit store, never from operator input, and
its SHA-256 is recorded so a printed report can be traced back (spec 9).
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
from pathlib import Path

from .. import APP_NAME, __version__
from ..audit.store import AuditStore

RESULT_WORDING = {
    "Completed and verified": "Sanitization completed and verified",
    "Completed with caveats": "Sanitization completed with caveats",
    "Inconclusive: sanitization not verified": "Sanitization NOT verified",
    "Failed": "Operation failed: drive NOT sanitized",
    "Interrupted": "Operation interrupted: drive NOT sanitized",
    "Blocked before execution": "Not executed: drive NOT sanitized",
}


def operation_report(store: AuditStore, op_id: str) -> dict:
    op = store.operation(op_id)
    if op is None:
        raise KeyError(op_id)
    chain_ok, chain_msg = store.verify_chain()
    context = next((e["data"] for e in store.events(op_id) if e["kind"] == "operation.context"), {})
    return {
        "context": context,
        "report_type": "EZWipe operation record",
        "software": {"name": APP_NAME, "version": __version__},
        "operation_id": op_id,
        "batch_id": op["batch_id"],
        "simulated": op["simulated"],
        "media": op["device"],
        "sanitization": {
            "method": op["method"],
            "profile": op["profile_id"],
            "policy_version": op["policy_version"],
            "created_at": op["created_at"],
            "started_at": op["started_at"],
            "ended_at": op["ended_at"],
            "timezone": "UTC",
            "state": op["state"],
            "result_statement": RESULT_WORDING.get(op["state"], op["state"]),
            "verdict": op["verdict"],
        },
        "audit": {
            "operator": op["operator_id"],
            "organization": op["organization"],
            "events": [{"ts": e["ts"], "kind": e["kind"], "data": e["data"], "hash": e["hash"]}
                       for e in store.events(op_id)],
            "chain_check": chain_msg if chain_ok else "FAILED: " + chain_msg,
        },
        "notice": "Follows NIST SP 800-88 Rev. 2 guidance. Not a NIST certification.",
    }


def write_json_report(store: AuditStore, op_id: str, out_dir: str | Path) -> Path:
    path, _ = write_json_report_with_digest(store, op_id, out_dir)
    return path


def write_json_report_with_digest(store: AuditStore, op_id: str, out_dir: str | Path) -> tuple[Path, str]:
    data = json.dumps(operation_report(store, op_id), indent=2, sort_keys=True, default=str)
    digest = hashlib.sha256(data.encode()).hexdigest()
    path = Path(out_dir) / f"{op_id}.json"
    path.write_text(data, encoding="utf-8")
    store.record_report(op_id, "json", digest, str(path))
    return path, digest


def history_csv(store: AuditStore) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["operation_id", "batch_id", "model", "serial", "capacity_bytes", "method",
                "state", "started_at", "ended_at", "simulated"])
    for op in store.operations(10_000):
        dv = op["device"]
        w.writerow([op["op_id"], op["batch_id"], dv["model"], dv["serial"], dv["capacity_bytes"],
                    op["method"], op["state"], op["started_at"], op["ended_at"], op["simulated"]])
    return buf.getvalue()
