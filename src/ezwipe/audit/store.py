"""Audit store on SQLite (spec 12).

- Events are append-only: triggers reject UPDATE and DELETE.
- Each event carries the hash of the previous one, so any edit to the
  history breaks the chain and is detected by verify_chain().
- A final operation state can never be changed.
- On start, operations left open by a crash are closed as "inconclusive".
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import uuid
from datetime import datetime
from pathlib import Path

from ..core.models import FINAL_STATES, Drive, OperationState, utc_now

SCHEMA = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS devices (
    device_key TEXT PRIMARY KEY,
    model TEXT NOT NULL,
    serial TEXT NOT NULL,
    capacity_bytes INTEGER NOT NULL,
    firmware TEXT,
    protocol TEXT,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS operators (
    operator_id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS profiles (
    profile_id TEXT PRIMARY KEY,
    definition_json TEXT NOT NULL,
    policy_version TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS operations (
    op_id TEXT PRIMARY KEY,
    batch_id TEXT,
    device_key TEXT NOT NULL REFERENCES devices(device_key),
    device_snapshot_json TEXT NOT NULL,
    method TEXT NOT NULL,
    profile_id TEXT,
    operator_id TEXT,
    organization TEXT,
    software_version TEXT NOT NULL,
    policy_version TEXT NOT NULL,
    simulated INTEGER NOT NULL,
    state TEXT NOT NULL,
    created_at TEXT NOT NULL,
    started_at TEXT,
    ended_at TEXT,
    verdict_json TEXT
);

CREATE TABLE IF NOT EXISTS events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    op_id TEXT,
    ts TEXT NOT NULL,
    kind TEXT NOT NULL,
    data_json TEXT NOT NULL,
    prev_hash TEXT NOT NULL,
    hash TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS reports (
    report_id TEXT PRIMARY KEY,
    op_id TEXT NOT NULL REFERENCES operations(op_id),
    created_at TEXT NOT NULL,
    format TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    path TEXT
);

CREATE TRIGGER IF NOT EXISTS events_no_update BEFORE UPDATE ON events
BEGIN SELECT RAISE(ABORT, 'audit events are append-only'); END;
CREATE TRIGGER IF NOT EXISTS events_no_delete BEFORE DELETE ON events
BEGIN SELECT RAISE(ABORT, 'audit events are append-only'); END;
CREATE TRIGGER IF NOT EXISTS operations_no_delete BEFORE DELETE ON operations
BEGIN SELECT RAISE(ABORT, 'operations cannot be deleted'); END;
CREATE TRIGGER IF NOT EXISTS operations_final_locked BEFORE UPDATE ON operations
WHEN OLD.state IN ({final})
BEGIN SELECT RAISE(ABORT, 'a final operation state cannot change'); END;
CREATE TRIGGER IF NOT EXISTS operations_identity_locked BEFORE UPDATE ON operations
WHEN NEW.device_key IS NOT OLD.device_key OR NEW.method IS NOT OLD.method
  OR NEW.device_snapshot_json IS NOT OLD.device_snapshot_json OR NEW.created_at IS NOT OLD.created_at
BEGIN SELECT RAISE(ABORT, 'operation identity fields cannot change'); END;
""".replace("{final}", ", ".join(f"'{s.value}'" for s in FINAL_STATES))

GENESIS = "0" * 64


def _ts(dt: datetime | None = None) -> str:
    return (dt or utc_now()).isoformat(timespec="milliseconds")


def _event_hash(prev: str, op_id: str | None, ts: str, kind: str, data_json: str) -> str:
    h = hashlib.sha256()
    for part in (prev, op_id or "", ts, kind, data_json):
        h.update(part.encode("utf-8"))
        h.update(b"\x1f")
    return h.hexdigest()


def drive_snapshot(drive: Drive) -> dict:
    i = drive.identity
    return {
        "model": i.model, "serial": i.serial, "capacity_bytes": i.capacity_bytes,
        "vendor": i.vendor, "firmware": i.firmware, "eui64": i.nvme_eui64, "nguid": i.nvme_nguid,
        "protocol": drive.protocol.value, "bus_type": drive.bus_type.value,
        "connection": drive.connection.value, "device_path": drive.device_path,
        "disk_number": drive.disk_number,
        "methods": {m.method.value: m.availability.value for m in drive.methods},
        "limitations": drive.limitations,
    }


class AuditStore:
    def __init__(self, path: str | Path):
        self.path = str(path)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._db.executescript(SCHEMA)

    def close(self):
        self._db.close()

    # ------------------------------------------------------------ events
    def append_event(self, kind: str, data: dict, op_id: str | None = None) -> str:
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                row = self._db.execute("SELECT hash FROM events ORDER BY seq DESC LIMIT 1").fetchone()
                prev = row["hash"] if row else GENESIS
                ts = _ts()
                data_json = json.dumps(data, sort_keys=True, default=str)
                h = _event_hash(prev, op_id, ts, kind, data_json)
                self._db.execute(
                    "INSERT INTO events (op_id, ts, kind, data_json, prev_hash, hash) VALUES (?,?,?,?,?,?)",
                    (op_id, ts, kind, data_json, prev, h))
                self._db.execute("COMMIT")
                return h
            except Exception:
                self._db.execute("ROLLBACK")
                raise

    def verify_chain(self) -> tuple[bool, str]:
        with self._lock:
            prev = GENESIS
            for r in self._db.execute("SELECT * FROM events ORDER BY seq"):
                if r["prev_hash"] != prev:
                    return False, f"Chain broken at event {r['seq']} (prev hash mismatch)"
                expect = _event_hash(prev, r["op_id"], r["ts"], r["kind"], r["data_json"])
                if expect != r["hash"]:
                    return False, f"Event {r['seq']} was modified"
                prev = r["hash"]
            return True, "Audit chain intact"

    def events(self, op_id: str | None = None) -> list[dict]:
        with self._lock:
            if op_id:
                rows = self._db.execute("SELECT * FROM events WHERE op_id=? ORDER BY seq", (op_id,))
            else:
                rows = self._db.execute("SELECT * FROM events ORDER BY seq")
            return [dict(r) | {"data": json.loads(r["data_json"])} for r in rows]

    # ------------------------------------------------------------ devices
    def record_device(self, drive: Drive) -> str:
        i = drive.identity
        key = i.key()
        now = _ts()
        with self._lock:
            self._db.execute(
                """INSERT INTO devices (device_key, model, serial, capacity_bytes, firmware, protocol,
                                        first_seen, last_seen)
                   VALUES (?,?,?,?,?,?,?,?)
                   ON CONFLICT(device_key) DO UPDATE SET last_seen=excluded.last_seen,
                                                        firmware=excluded.firmware""",
                (key, i.model, i.serial, i.capacity_bytes, i.firmware, drive.protocol.value, now, now))
        return key

    # ------------------------------------------------------------ operations
    def create_operation(self, drive: Drive, method: str, *, batch_id: str | None,
                         profile_id: str | None, operator_id: str | None, organization: str | None,
                         software_version: str, policy_version: str, simulated: bool) -> str:
        op_id = "EZW-" + uuid.uuid4().hex[:12].upper()
        key = self.record_device(drive)
        snap = json.dumps(drive_snapshot(drive), sort_keys=True)
        with self._lock:
            self._db.execute(
                """INSERT INTO operations (op_id, batch_id, device_key, device_snapshot_json, method,
                   profile_id, operator_id, organization, software_version, policy_version, simulated,
                   state, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (op_id, batch_id, key, snap, method, profile_id, operator_id, organization,
                 software_version, policy_version, int(simulated), OperationState.WAITING.value, _ts()))
        self.append_event("operation.created", {"device_key": key, "method": method,
                                                "batch_id": batch_id, "simulated": simulated}, op_id)
        return op_id

    def set_state(self, op_id: str, state: OperationState, *, detail: dict | None = None,
                  verdict: dict | None = None) -> None:
        with self._lock:
            sets, args = ["state=?"], [state.value]
            if state == OperationState.RUNNING:
                sets.append("started_at=COALESCE(started_at, ?)")
                args.append(_ts())
            if state.is_final:
                sets.append("ended_at=?")
                args.append(_ts())
            if verdict is not None:
                sets.append("verdict_json=?")
                args.append(json.dumps(verdict, sort_keys=True, default=str))
            args.append(op_id)
            self._db.execute(f"UPDATE operations SET {', '.join(sets)} WHERE op_id=?", args)
        self.append_event("operation.state", {"state": state.value, **(detail or {})}, op_id)

    def operation(self, op_id: str) -> dict | None:
        with self._lock:
            r = self._db.execute("SELECT * FROM operations WHERE op_id=?", (op_id,)).fetchone()
        return self._op_dict(r) if r else None

    def operations(self, limit: int = 500) -> list[dict]:
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM operations ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [self._op_dict(r) for r in rows]

    def history_for(self, device_key: str) -> list[dict]:
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM operations WHERE device_key=? ORDER BY created_at DESC", (device_key,)).fetchall()
        return [self._op_dict(r) for r in rows]

    @staticmethod
    def _op_dict(r) -> dict:
        d = dict(r)
        d["device"] = json.loads(d.pop("device_snapshot_json"))
        d["verdict"] = json.loads(d["verdict_json"]) if d.get("verdict_json") else None
        d.pop("verdict_json", None)
        d["simulated"] = bool(d["simulated"])
        return d

    def recover_open_operations(self) -> list[str]:
        """Close operations left open by a crash as inconclusive (spec 6.4)."""
        final = tuple(s.value for s in FINAL_STATES)
        with self._lock:
            rows = self._db.execute(
                f"SELECT op_id, state FROM operations WHERE state NOT IN ({','.join('?' * len(final))})",
                final).fetchall()
        recovered = []
        for r in rows:
            was_running = r["state"] not in (OperationState.WAITING.value, OperationState.CHECKING.value,
                                             OperationState.READY.value)
            state = OperationState.INCONCLUSIVE if was_running else OperationState.INTERRUPTED
            self.set_state(r["op_id"], state, detail={
                "reason": "EZWipe closed unexpectedly while the operation was open",
                "previous_state": r["state"]},
                verdict={"state": state.value, "summary": "Result could not be determined after an "
                         "unexpected shutdown. Check the drive's sanitize status before reuse.",
                         "caveats": []})
            recovered.append(r["op_id"])
        return recovered

    def reports(self, op_id: str | None = None) -> list[dict]:
        with self._lock:
            if op_id:
                rows = self._db.execute("SELECT * FROM reports WHERE op_id=? ORDER BY created_at", (op_id,))
            else:
                rows = self._db.execute("SELECT * FROM reports ORDER BY created_at")
            return [dict(r) for r in rows]

    def record_report(self, op_id: str, fmt: str, sha256: str, path: str | None) -> str:
        rid = "RPT-" + uuid.uuid4().hex[:12].upper()
        with self._lock:
            self._db.execute("INSERT INTO reports VALUES (?,?,?,?,?,?)",
                             (rid, op_id, _ts(), fmt, sha256, path))
        self.append_event("report.created", {"report_id": rid, "format": fmt, "sha256": sha256}, op_id)
        return rid
