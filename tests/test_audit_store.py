import sqlite3

import pytest

from ezwipe.audit.store import AuditStore
from ezwipe.backends.simulated import default_lab
from ezwipe.core.models import OperationState
from ezwipe.reports.export import operation_report


def _op(store):
    d = default_lab().drives[1].drive
    return store.create_operation(d, "nvme.sanitize.crypto_erase", batch_id="B", profile_id="p",
                                  operator_id="o", organization="org", software_version="0",
                                  policy_version="p0", simulated=True)


def test_events_are_append_only(store):
    _op(store)
    with pytest.raises(sqlite3.DatabaseError):
        store._db.execute("UPDATE events SET kind='x'")
    with pytest.raises(sqlite3.DatabaseError):
        store._db.execute("DELETE FROM events")


def test_chain_detects_tampering(tmp_path):
    s = AuditStore(tmp_path / "a.db")
    _op(s)
    assert s.verify_chain()[0]
    s._db.execute("DROP TRIGGER events_no_update")
    s._db.execute("UPDATE events SET data_json='{}' WHERE seq=1")
    ok, msg = s.verify_chain()
    assert not ok and "modified" in msg


def test_final_state_is_locked(store):
    op = _op(store)
    store.set_state(op, OperationState.FAILED)
    with pytest.raises(sqlite3.DatabaseError):
        store.set_state(op, OperationState.COMPLETED_VERIFIED)


def test_crash_recovery_marks_inconclusive(tmp_path):
    path = tmp_path / "a.db"
    s = AuditStore(path)
    op = _op(s)
    s.set_state(op, OperationState.RUNNING)
    s.close()
    s2 = AuditStore(path)
    assert s2.recover_open_operations() == [op]
    assert s2.operation(op)["state"] == OperationState.INCONCLUSIVE.value


def test_report_never_claims_success_for_failed(store):
    op = _op(store)
    store.set_state(op, OperationState.FAILED, verdict={"state": "Failed"})
    r = operation_report(store, op)
    assert "NOT sanitized" in r["sanitization"]["result_statement"]
    assert "Not a NIST certification" in r["notice"]
