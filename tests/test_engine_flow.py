from ezwipe.backends.simulated import Scenario
from ezwipe.core.models import OperationState
from tests.conftest import by_serial


def run(engine, drives, confirm=True, profile="nvme-purge"):
    plan = engine.plan(drives, profile)
    codes = {i.drive.identity.key(): (i.confirmation_code if confirm else "XXXX") for i in plan.items}
    events = []
    ops = engine.start(plan, codes, lambda *a: events.append(a))
    engine.wait(10)
    return [engine.store.operation(o) for o in ops], events


def test_success_is_verified_with_simulated_caveat(engine):
    [op], _ = run(engine, [by_serial(engine, "SIM-D4-0001")])
    assert op["state"] == OperationState.COMPLETED_WITH_CAVEATS.value
    assert any("SIMULATED" in c for c in op["verdict"]["caveats"])
    assert op["simulated"]


def test_wrong_confirmation_blocks(engine, lab):
    [op], _ = run(engine, [by_serial(engine, "SIM-D4-0001")], confirm=False)
    assert op["state"] == OperationState.BLOCKED.value
    assert not lab.drives[1].sanitized


def test_device_failure(engine):
    [op], _ = run(engine, [by_serial(engine, "SIM-D4-0004")])
    assert op["state"] == OperationState.FAILED.value


def test_no_device_status_is_inconclusive(engine):
    [op], _ = run(engine, [by_serial(engine, "SIM-D4-0003")])
    assert op["state"] == OperationState.INCONCLUSIVE.value


def test_disconnect_and_timeout_are_inconclusive(engine, lab):
    lab.drives[1].scenario = Scenario.DISCONNECT
    lab.drives[2].scenario = Scenario.TIMEOUT
    ops, _ = run(engine, [by_serial(engine, "SIM-D4-0001"), by_serial(engine, "SIM-D4-0002")])
    assert [o["state"] for o in ops] == [OperationState.INCONCLUSIVE.value] * 2


def test_identity_change_stops_before_execution(engine, lab):
    lab.drives[1].scenario = Scenario.IDENTITY_CHANGE
    [op], _ = run(engine, [by_serial(engine, "SIM-D4-0001")])
    assert op["state"] == OperationState.BLOCKED.value
    assert not lab.drives[1].sanitized
    kinds = [e["kind"] for e in engine.store.events(op["op_id"])]
    assert "operation.evidence" not in kinds      # nothing was sent


def test_unexpected_response_fails(engine, lab):
    lab.drives[1].scenario = Scenario.UNEXPECTED_RESPONSE
    [op], _ = run(engine, [by_serial(engine, "SIM-D4-0001")])
    assert op["state"] == OperationState.FAILED.value


def test_batch_runs_sequentially_and_blocks_system_disk(engine):
    drives = [by_serial(engine, s) for s in ("SIM-SYS-0001", "SIM-D4-0001", "SIM-D4-0002")]
    ops, events = run(engine, drives)
    assert ops[0]["state"] == OperationState.BLOCKED.value
    assert ops[1]["state"] != OperationState.BLOCKED.value
    # Sequential: the second drive never runs while the first is running.
    running = [e[0] for e in events if e[1] == OperationState.RUNNING]
    assert running == [ops[1]["op_id"], ops[2]["op_id"]]


def test_cancel_marks_interrupted(engine, lab):
    lab.step_delay = 0.05
    plan = engine.plan([by_serial(engine, "SIM-D4-0001")], "nvme-purge")
    codes = {i.drive.identity.key(): i.confirmation_code for i in plan.items}
    seen = []

    def listener(op_id, state, msg, frac):
        seen.append(state)
        if state == OperationState.WAITING_DEVICE and len(seen) < 8:
            engine.cancel(op_id)

    [op_id] = engine.start(plan, codes, listener)
    engine.wait(10)
    assert engine.store.operation(op_id)["state"] == OperationState.INTERRUPTED.value
