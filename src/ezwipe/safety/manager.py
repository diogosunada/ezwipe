"""Safety Manager: blocks wrong-target and unsafe operations (spec 7).

Rules are fail-closed: when something cannot be confirmed, the answer is
"blocked".
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..core.models import Drive, DriveIdentity, MethodId


@dataclass
class SafetyVerdict:
    allowed: bool
    reasons: list[str] = field(default_factory=list)


class SafetyManager:
    def __init__(self, allow_mounted_volumes: bool = False):
        self.allow_mounted_volumes = allow_mounted_volumes

    def check_target(self, drive: Drive, method: MethodId | None = None) -> SafetyVerdict:
        reasons = []
        if drive.is_system_disk:
            reasons.append(drive.protection_reason or "This drive holds the running operating system.")
        if drive.hosts_page_file:
            reasons.append("This drive holds the Windows page file.")
        if not drive.identity_confident:
            reasons.append("The drive's identity or role could not be confirmed.")
        if not drive.identity.serial.strip():
            reasons.append("No serial number; the target cannot be re-checked before execution.")
        if drive.mounted_volumes and not self.allow_mounted_volumes:
            reasons.append(f"The drive has mounted volumes ({', '.join(drive.mounted_volumes)}). "
                           "They must be taken offline first.")
        if method is not None and method not in drive.available_methods:
            reasons.append(f"{method.value} is not available for this drive through Windows.")
        return SafetyVerdict(not reasons, reasons)

    def revalidate(self, planned: DriveIdentity, current: DriveIdentity | None) -> SafetyVerdict:
        """Right before the destructive call: is it still the same drive?"""
        if current is None:
            return SafetyVerdict(False, ["The drive is no longer present."])
        problems = planned.mismatches(current)
        if problems:
            return SafetyVerdict(False, ["Drive identity changed since confirmation: " + "; ".join(problems)])
        return SafetyVerdict(True)

    @staticmethod
    def confirmation_code(drive: Drive) -> str:
        """What the operator must type to confirm: last 4 characters of the serial."""
        s = drive.identity.serial.strip()
        return s[-4:].upper() if s else ""
