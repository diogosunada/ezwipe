"""Sanitization Engine and Operation Manager (spec 4, 6, 11.3).

The GUI asks the engine for a plan, shows it, collects the operator's
confirmation and hands it back. The engine then, for each drive, in order:

1. validates the drive (Safety Manager),
2. checks the profile (Policy Manager),
3. re-checks the method is reachable,
4. checks the confirmation code,
5. re-reads the drive identity right before the command,
6. executes through the backend,
7. follows the device to completion,
8. runs method checks and classifies the evidence,
9. records everything in the audit store.

Batches run one drive at a time (spec 6.2).
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from typing import Callable

from .. import __version__
from ..audit.store import AuditStore
from ..backends.base import backend_for
from ..capability import detector
from ..core.methods import POLICY_VERSION, method_info
from ..core.models import Availability, Drive, MethodId, OperationState
from ..policy.profiles import PolicyManager
from ..safety.manager import SafetyManager
from ..verification.engine import classify

Listener = Callable[[str, OperationState, str, float | None], None]
"""(op_id, state, message, device progress 0..1 or None)."""


@dataclass
class PlanItem:
    drive: Drive
    method: MethodId | None
    reason: str
    allowed: bool
    blockers: list[str] = field(default_factory=list)
    confirmation_code: str = ""
    options: dict = field(default_factory=dict)

    @property
    def scope(self) -> str:
        return method_info(self.method).scope if self.method else ""


@dataclass
class BatchPlan:
    batch_id: str
    profile_id: str
    items: list[PlanItem]

    @property
    def runnable(self) -> list[PlanItem]:
        return [i for i in self.items if i.allowed]


class Engine:
    def __init__(self, discovery, backends: list, store: AuditStore,
                 safety: SafetyManager | None = None, policy: PolicyManager | None = None,
                 operator_id: str | None = None, organization: str | None = None,
                 context: dict | None = None):
        self.discovery = discovery
        self.backends = backends
        self.store = store
        self.safety = safety or SafetyManager()
        self.policy = policy or PolicyManager()
        self.operator_id = operator_id
        self.organization = organization
        # Recorded with every operation: host system info, operator fields from config.
        self.context = context or {}
        self.simulated = getattr(discovery, "name", "") == "simulated"
        self._active: dict[str, str] = {}          # device_key -> op_id
        self._cancel: dict[str, threading.Event] = {}
        self._lock = threading.Lock()
        self._threads: list[threading.Thread] = []
        self.drives: list[Drive] = []
        recovered = store.recover_open_operations()
        store.append_event("app.started", {"version": __version__, "recovered": recovered,
                                           "simulated": self.simulated})

    # ----------------------------------------------------------- discovery
    def refresh(self) -> list[Drive]:
        drives = self.discovery.discover()
        for d in drives:
            detector.evaluate(d, self.backends)
        self.drives = drives
        self.store.append_event("discovery", {
            "count": len(drives),
            "drives": [{"key": d.identity.key(), "path": d.device_path, "bus": d.bus_type.value,
                        "available": [m.value for m in d.available_methods],
                        "protected": d.is_protected} for d in drives]})
        return drives

    def backend_for(self, drive: Drive, method: MethodId | None = None):
        return backend_for(self.backends, drive, method)

    def frozen_drives(self) -> list[Drive]:
        return [d for d in self.drives if (d.capabilities.get("ata") or {}).get("frozen")]

    def can_unfreeze(self) -> bool:
        return any(hasattr(b, "unfreeze") for b in self.backends)

    def unfreeze(self) -> str:
        """Sleep and wake the machine so the BIOS security freeze lifts.

        Only called after the operator asks for it. Nothing is erased.
        """
        b = next((b for b in self.backends if hasattr(b, "unfreeze")), None)
        if b is None:
            return "Unfreeze is not available in this edition."
        frozen = [d.identity.key() for d in self.frozen_drives()]
        self.store.append_event("maintenance.unfreeze.start", {"frozen": frozen})
        result = b.unfreeze()
        self.refresh()
        still = [d.identity.key() for d in self.frozen_drives()]
        self.store.append_event("maintenance.unfreeze.end", {"result": result, "still_frozen": still})
        return result if not still else f"{result} Still frozen: {len(still)} drive(s)."

    def is_busy(self, drive: Drive) -> bool:
        with self._lock:
            return drive.identity.key() in self._active

    # ----------------------------------------------------------- planning
    def plan(self, drives: list[Drive], profile_id: str, method: MethodId | None = None) -> BatchPlan:
        profile = self.policy.get(profile_id)
        items = []
        for d in drives:
            if method is None:
                m, why = detector.recommend(d, list(profile.allowed_methods))
            else:
                m, why = method, "Chosen by the operator."
            blockers = []
            if m is None:
                blockers.append(why)
            else:
                ok, msg = profile.permits(m)
                if not ok:
                    blockers.append(msg)
            blockers += self.safety.check_target(d, m).reasons
            if self.is_busy(d):
                blockers.append("An operation is already running on this drive.")
            items.append(PlanItem(d, m, why, not blockers, blockers, self.safety.confirmation_code(d),
                                  dict(profile.options)))
        return BatchPlan("BATCH-" + uuid.uuid4().hex[:8].upper(), profile_id, items)

    def plan_all(self, profile_id: str) -> BatchPlan:
        """Plan every drive that is not protected (used by auto mode)."""
        return self.plan([d for d in self.drives if not d.is_protected], profile_id)

    # ----------------------------------------------------------- execution
    def start(self, plan: BatchPlan, confirmations: dict[str, str], listener: Listener,
              auto_confirmed_by: str | None = None) -> list[str]:
        """Create operations and run them sequentially in a worker thread.

        confirmations maps drive key -> code typed by the operator.
        auto_confirmed_by: set only by auto mode, naming the config that allowed it
        (e.g. "ezwipe.toml sha256=..."). The codes are then filled in here and the
        audit record says no one typed them.
        Returns the operation ids (blocked items get a BLOCKED record too).
        """
        if auto_confirmed_by:
            confirmations = {i.drive.identity.key(): i.confirmation_code for i in plan.items}
        queue: list[tuple[str, PlanItem]] = []
        op_ids = []
        for item in plan.items:
            d = item.drive
            op_id = self.store.create_operation(
                d, item.method.value if item.method else "none", batch_id=plan.batch_id,
                profile_id=plan.profile_id, operator_id=self.operator_id,
                organization=self.organization, software_version=__version__,
                policy_version=POLICY_VERSION, simulated=self.simulated)
            op_ids.append(op_id)
            self.store.append_event("operation.context", {
                **self.context, "options": item.options,
                "profile_name": self.policy.get(plan.profile_id).name,
                "confirmation": ("auto: " + auto_confirmed_by) if auto_confirmed_by else "typed"},
                op_id)
            typed = (confirmations.get(d.identity.key()) or "").strip().upper()
            blockers = list(item.blockers)
            if item.allowed and typed != item.confirmation_code:
                blockers.append("Confirmation code did not match.")
            if blockers:
                self.store.set_state(op_id, OperationState.BLOCKED, detail={"blockers": blockers},
                                     verdict={"state": OperationState.BLOCKED.value,
                                              "summary": "Not executed.", "caveats": blockers})
                listener(op_id, OperationState.BLOCKED, "; ".join(blockers), None)
                continue
            with self._lock:
                if d.identity.key() in self._active:
                    self.store.set_state(op_id, OperationState.BLOCKED,
                                         detail={"blockers": ["duplicate operation"]})
                    listener(op_id, OperationState.BLOCKED, "Duplicate operation", None)
                    continue
                self._active[d.identity.key()] = op_id
                self._cancel[op_id] = threading.Event()
            self.store.set_state(op_id, OperationState.READY,
                                 detail={"confirmed_code": typed, "operator": self.operator_id})
            listener(op_id, OperationState.READY, "Confirmed", None)
            queue.append((op_id, item))

        t = threading.Thread(target=self._run_queue, args=(queue, listener), daemon=True,
                             name=f"ezwipe-{plan.batch_id}")
        self._threads.append(t)
        t.start()
        return op_ids

    def cancel(self, op_id: str) -> None:
        ev = self._cancel.get(op_id)
        if ev:
            ev.set()
            self.store.append_event("operation.cancel_requested", {}, op_id)

    def wait(self, timeout: float | None = None) -> None:
        for t in list(self._threads):
            t.join(timeout)

    def _run_queue(self, queue, listener: Listener) -> None:
        for op_id, item in queue:
            try:
                self._run_one(op_id, item, listener)
            except Exception as e:  # never leave an operation open
                state = OperationState.INCONCLUSIVE
                self.store.set_state(op_id, state, detail={"exception": repr(e)},
                                     verdict={"state": state.value,
                                              "summary": "Internal error during the operation. "
                                                         "Result could not be determined.",
                                              "caveats": [repr(e)]})
                listener(op_id, state, f"Internal error: {e}", None)
            finally:
                with self._lock:
                    self._active.pop(item.drive.identity.key(), None)

    def _transition(self, op_id, state, listener, msg="", progress=None, **detail):
        self.store.set_state(op_id, state, detail={"message": msg, **detail} if (msg or detail) else None)
        listener(op_id, state, msg, progress)

    def _finish(self, op_id, state, listener, summary, caveats, extra=None):
        verdict = {"state": state.value, "summary": summary, "caveats": caveats, **(extra or {})}
        self.store.set_state(op_id, state, verdict=verdict)
        listener(op_id, state, summary, None)

    def _run_one(self, op_id: str, item: PlanItem, listener: Listener) -> None:
        d, method = item.drive, item.method
        cancel = self._cancel[op_id]
        backend = self.backend_for(d, method)

        self._transition(op_id, OperationState.CHECKING, listener, "Re-checking drive and method")
        if backend is None:
            return self._finish(op_id, OperationState.BLOCKED, listener, "No backend for this drive.", [])
        avail, reason = backend.reachability(d, method)
        if avail != Availability.AVAILABLE:
            return self._finish(op_id, OperationState.BLOCKED, listener,
                                f"Method no longer reachable: {reason}", [])
        current = backend.read_identity(d)
        verdict = self.safety.revalidate(d.identity, current)
        if not verdict.allowed:
            return self._finish(op_id, OperationState.BLOCKED, listener,
                                "Stopped before execution: " + "; ".join(verdict.reasons), verdict.reasons)
        if cancel.is_set():
            return self._finish(op_id, OperationState.INTERRUPTED, listener,
                                "Cancelled before execution. Nothing was sent to the drive.", [])

        self._transition(op_id, OperationState.RUNNING, listener, f"Sending {method_info(method).name}",
                         backend=backend.name)

        def progress(msg: str, frac: float | None):
            self.store.append_event("operation.progress", {"message": msg, "fraction": frac}, op_id)
            listener(op_id, OperationState.WAITING_DEVICE, msg, frac)

        evidence = backend.execute(d, method, cancel, progress, item.options)
        self.store.append_event("operation.evidence", evidence.__dict__, op_id)

        checks = []
        if evidence.device_completed:
            self._transition(op_id, OperationState.VERIFYING, listener, "Running method checks")
            checks = backend.post_checks(d, method, evidence)
            self.store.append_event("operation.checks", {"checks": checks}, op_id)

        v = classify(method, evidence, checks, cancelled=cancel.is_set())
        self._finish(op_id, v.state, listener, v.summary, v.caveats,
                     {"evidence_level": v.level.name, "checks": checks,
                      "method_name": method_info(method).name,
                      "parameters": evidence.parameters,
                      "nist_category": method_info(method).category.value,
                      "route": evidence.route, "device_status": evidence.device_status,
                      "errors": evidence.errors})
