"""GUI tests with simulated drives, offscreen."""

import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)


@pytest.fixture
def window(tmp_path):
    from ezwipe.ui.app import MainWindow, build
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    engine, store, edition, settings, data = build(True, tmp_path / "a.db", data=tmp_path)
    engine.backends[0].lab.step_delay = 0.001
    w = MainWindow(engine, store, edition, settings, data)
    yield w, engine, store, app
    engine.wait(10)
    end = time.time() + 0.6          # let delayed refreshes fire while the store is open
    while time.time() < end:
        app.processEvents()
        time.sleep(0.02)
    w.close()
    w.deleteLater()
    app.processEvents()
    store.close()


def pump(app, engine, seconds=10):
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        if not engine._active:
            app.processEvents()
            time.sleep(0.05)
            app.processEvents()
            return
        time.sleep(0.01)


def test_main_window_builds(window):
    w, engine, store, app = window
    assert len(w.cards) == 8
    assert w.tiles["protected"].value.text() == "1"
    # Best Purge: 4 NVMe + the SATA HDD (enhanced secure erase); frozen SSD and USB stick are not ready.
    assert w.tiles["ready"].value.text() == "5"
    w.set_profile("nist-clear")
    assert w.tiles["ready"].value.text() == "7"


def test_select_erase_and_certificate(window):
    w, engine, store, app = window
    key = next(k for k, c in w.cards.items() if c.drive.identity.serial == "SIM-D4-0001")
    w.set_selection({key})
    assert w.erase_btn.isEnabled() and "1 drive" in w.erase_btn.text()
    plan = engine.plan([w.cards[key].drive], w.profile_id)
    w._launch(plan, {plan.items[0].drive.identity.key(): plan.items[0].confirmation_code})
    pump(app, engine)
    st = w.status[key]
    assert st.op_state.is_final and st.op_state.value.startswith("Completed")
    assert st.certificate.endswith(".pdf") and open(st.certificate, "rb").read(4) == b"%PDF"
    assert w.cards[key].cert_btn.isVisibleTo(w.cards[key])
    w.tabs.setCurrentIndex(1)
    assert w.history.table.rowCount() == 1


def test_auto_mode_launch_records_who_confirmed(window):
    w, engine, store, app = window
    plan = engine.plan_all("auto-purge")
    plan.items = plan.runnable
    w._launch(plan, {}, auto_by="ezwipe.toml sha256=abc")
    pump(app, engine, 20)
    ctx = [e for e in store.events() if e["kind"] == "operation.context"]
    assert ctx and all(e["data"]["confirmation"] == "auto: ezwipe.toml sha256=abc" for e in ctx)
    states = {s.op_state.value for s in w.status.values() if s.op_state}
    assert any(s.startswith("Completed") for s in states) and "Failed" in states


def add_d4_enclosure(lab):
    """Four USB bays that all report the enclosure's serial, like the TerraMaster D4."""
    from dataclasses import replace

    from ezwipe.backends.simulated import SimDrive
    from ezwipe.core.models import BusType, ConnectionPath, Drive, DriveIdentity, Protocol
    ident = DriveIdentity(serial="202510233580", model="TM D4 SSD", capacity_bytes=256 * 10**9)
    base = Drive(device_path="", disk_number=0, identity=ident, protocol=Protocol.UNKNOWN,
                 bus_type=BusType.USB, connection=ConnectionPath.USB_BRIDGE, is_ssd=True)
    for n in range(10, 14):
        lab.drives.append(SimDrive(replace(base, device_path=f"/dev/sim/sd{chr(87 + n)}", disk_number=n)))


def test_shared_serial_drives_get_own_cards_and_are_protected(window):
    w, engine, store, app = window
    add_d4_enclosure(engine.discovery.lab)
    w.refresh()
    d4 = [c for c in w.cards.values() if c.drive.identity.serial == "202510233580"]
    assert len(d4) == 4                                   # one card per bay
    assert len({c.drive.device_path for c in d4}) == 4
    for c in d4:
        assert c.drive.is_protected and not c.selectable
        assert "cannot tell them apart" in c.method.text()
    w.set_profile("nist-clear")
    w.select_all_ready()
    assert not any(c.check.isChecked() for c in d4)
    plan = engine.plan([c.drive for c in d4], "nist-clear")
    assert not plan.runnable


def test_overwrite_on_usb_stick(window):
    w, engine, store, app = window
    w.set_profile("zero-fill")
    key = next(k for k, c in w.cards.items() if c.drive.kind == "USB")
    assert w.cards[key].selectable
    plan = engine.plan([w.cards[key].drive], "zero-fill")
    w._launch(plan, {plan.items[0].drive.identity.key(): plan.items[0].confirmation_code})
    pump(app, engine)
    assert w.status[key].op_state.value.startswith("Completed")
