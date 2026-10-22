import pytest

from ezwipe.audit.store import AuditStore
from ezwipe.backends.simulated import SimulatedBackend, SimulatedDiscovery, default_lab
from ezwipe.engine.engine import Engine


@pytest.fixture
def lab():
    lab = default_lab()
    lab.step_delay = 0.001
    return lab


@pytest.fixture
def store(tmp_path):
    s = AuditStore(tmp_path / "audit.db")
    yield s
    s.close()


@pytest.fixture
def engine(lab, store):
    e = Engine(SimulatedDiscovery(lab), [SimulatedBackend(lab)], store, operator_id="tester")
    e.refresh()
    return e


def by_serial(engine, serial):
    return next(d for d in engine.drives if d.identity.serial == serial)
