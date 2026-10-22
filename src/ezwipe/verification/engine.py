"""Verification Engine: turns evidence into a result state (spec 8).

Rules:
- A finished process is never success on its own.
- Device completion without passing method checks is "inconclusive".
- An inconclusive result is never upgraded automatically.
- NIST Rev. 2 separates verification (this module) from validation (a
  management decision recorded later), so the best state here is
  "Completed and verified", not "validated".
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..core.methods import method_info
from ..core.models import EvidenceLevel, ExecutionEvidence, MethodId, OperationState


@dataclass
class Verdict:
    state: OperationState
    level: EvidenceLevel
    summary: str
    caveats: list[str] = field(default_factory=list)


def evidence_level(ev: ExecutionEvidence, checks: list[dict]) -> EvidenceLevel:
    if not ev.accepted:
        return EvidenceLevel.NONE
    if not ev.device_started:
        return EvidenceLevel.COMMAND_ACCEPTED
    if not ev.device_completed:
        return EvidenceLevel.DEVICE_STARTED
    decided = [c for c in checks if c.get("passed") is not None]
    if decided and all(c["passed"] for c in decided):
        return EvidenceLevel.CRITERIA_MET
    return EvidenceLevel.DEVICE_COMPLETED


def classify(method: MethodId, ev: ExecutionEvidence, checks: list[dict],
             cancelled: bool = False) -> Verdict:
    level = evidence_level(ev, checks)
    info = method_info(method)
    caveats = []
    if info.category_provisional:
        caveats.append(f"NIST category '{info.category.value}' is provisional pending IEEE 2883 review.")
    if ev.route.startswith("SIMULATED"):
        caveats.append("SIMULATED operation. Not evidence of any real drive being sanitized.")

    if cancelled and not ev.device_completed:
        return Verdict(OperationState.INTERRUPTED, level,
                       "Stopped before the device reported completion. The drive may be "
                       "partly sanitized and must be treated as not sanitized.", caveats)
    if ev.disconnected or ev.timed_out:
        what = "disconnected" if ev.disconnected else "timed out"
        return Verdict(OperationState.INCONCLUSIVE, level,
                       f"The operation {what} before completion was confirmed. "
                       "Reconnect the drive and check its sanitize status.", caveats)
    if not ev.accepted:
        kind = "communication failure" if ev.communication_failure else "command rejected"
        return Verdict(OperationState.FAILED, level, f"Not executed ({kind}).",
                       caveats + ev.errors)
    if ev.errors and not ev.device_completed:
        return Verdict(OperationState.FAILED, level, "The device or driver reported a failure.",
                       caveats + ev.errors)
    if not ev.device_completed:
        return Verdict(OperationState.INCONCLUSIVE, level,
                       "The command finished but the device gave no completion status. "
                       "Sanitization not verified.", caveats)
    failed = [c for c in checks if c.get("passed") is False]
    if failed:
        return Verdict(OperationState.FAILED, level,
                       "The device reported completion but post-checks failed.",
                       caveats + [c["check"] for c in failed])
    passed = [c for c in checks if c.get("passed") is True]
    if not passed:
        return Verdict(OperationState.INCONCLUSIVE, level,
                       "The device reported completion, but no method-specific check could "
                       "confirm it. Sanitization not verified.", caveats)
    for c in checks:
        if c.get("passed") is None:
            caveats.append(f"Check could not decide: {c['check']}")
    if ev.errors:
        return Verdict(OperationState.COMPLETED_WITH_CAVEATS, level,
                       "Completed and checked, with warnings.", caveats + ev.errors)
    if caveats:
        return Verdict(OperationState.COMPLETED_WITH_CAVEATS, level,
                       "Completed and checked. See caveats.", caveats)
    return Verdict(OperationState.COMPLETED_VERIFIED, level, "Completed and verified.", caveats)
