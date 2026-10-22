"""Sanitization profiles and acceptance criteria (spec 4.6).

A profile never promises a category the method cannot support: if the
chosen method's category is weaker than the profile requires, the engine
refuses to plan it.

Profiles also carry the options the backends need: how much to read back
after a host overwrite, and whether a host protected area (HPA) may be
opened before erasing.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..core.methods import POLICY_VERSION, method_info
from ..core.models import MethodId, NistCategory

_RANK = {NistCategory.NONE: 0, NistCategory.CLEAR: 1, NistCategory.PURGE: 2, NistCategory.DESTROY: 3}

M = MethodId
FIRMWARE_PURGE = (M.NVME_SANITIZE_CRYPTO, M.NVME_SANITIZE_BLOCK, M.NVME_SANITIZE_OVERWRITE,
                  M.ATA_SANITIZE_CRYPTO, M.ATA_SANITIZE_BLOCK, M.ATA_SANITIZE_OVERWRITE,
                  M.ATA_SECURITY_ERASE_ENHANCED, M.NVME_FORMAT_CRYPTO)


@dataclass(frozen=True)
class Profile:
    profile_id: str
    name: str
    required_category: NistCategory
    allowed_methods: tuple[MethodId, ...]
    description: str = ""
    policy_version: str = POLICY_VERSION
    acceptance: tuple[str, ...] = field(default_factory=tuple)
    verify_fraction: float = 0.1      # host overwrite read-back: 1.0 full, 0.1 sampled
    reset_hidden_areas: bool = True   # open an ATA HPA before erasing, and record it
    tagline: str = ""                 # one line for the profile picker
    legacy: bool = False

    def permits(self, method: MethodId) -> tuple[bool, str]:
        if method not in self.allowed_methods:
            return False, f"{method.value} is not allowed by profile '{self.name}'."
        cat = method_info(method).category
        if _RANK[cat] < _RANK[self.required_category]:
            return False, (f"{method.value} is {cat.value}; profile '{self.name}' requires "
                           f"{self.required_category.value}.")
        return True, ""

    @property
    def options(self) -> dict:
        return {"verify_fraction": self.verify_fraction,
                "reset_hidden_areas": self.reset_hidden_areas}


DEFAULT_PROFILES = [
    Profile(
        "auto-purge", "Best Purge",
        NistCategory.PURGE, FIRMWARE_PURGE,
        "The strongest firmware method each drive supports: NVMe or ATA Sanitize, "
        "then ATA Enhanced Secure Erase. Drives without one are not touched.",
        acceptance=("Device reports success through its own status",
                    "Sample reads show no pre-sanitize data"),
        tagline="Recommended. Fastest and reaches hidden areas.",
    ),
    Profile(
        "nist-clear", "NIST Clear",
        NistCategory.CLEAR,
        FIRMWARE_PURGE + (M.ATA_SECURITY_ERASE, M.NVME_FORMAT_USER_DATA,
                          M.OVERWRITE_ADDRESSABLE, M.OVERWRITE_ZERO),
        "Firmware method when available, otherwise one host overwrite pass with a full "
        "read-back. Works on USB sticks and drives behind bridges.",
        acceptance=("Method completes", "Full read-back of the written pattern"),
        verify_fraction=1.0,
        tagline="Works on every writable drive. Full verify.",
    ),
    Profile(
        "zero-fill", "Quick zero fill",
        NistCategory.CLEAR, (M.OVERWRITE_ZERO,),
        "One pass of zeros with a 10% sampled read-back. For reuse inside your own "
        "organisation.",
        acceptance=("Every block written", "10% sampled read-back"),
        verify_fraction=0.1,
        tagline="Fast refresh for internal reuse.",
    ),
    Profile(
        "dod-3pass", "DoD 5220.22-M (3 passes)",
        NistCategory.CLEAR, (M.OVERWRITE_DOD_3PASS,),
        "Zeros, ones, then random data, last pass fully verified. Legacy standard kept "
        "for customers whose contracts name it.",
        acceptance=("All three passes written", "Full read-back of the last pass"),
        verify_fraction=1.0,
        tagline="Legacy. Only if a contract names it.",
        legacy=True,
    ),
    Profile(
        "nvme-purge", "NVMe Purge",
        NistCategory.PURGE,
        (M.NVME_SANITIZE_CRYPTO, M.NVME_SANITIZE_BLOCK, M.NVME_SANITIZE_OVERWRITE),
        "NVMe Sanitize only. Format NVM is not accepted for this profile.",
        acceptance=("Device reports sanitize success in the Sanitize Status log",
                    "Sample reads show no pre-sanitize data"),
        tagline="NVMe Sanitize only.",
    ),
    Profile(
        "nvme-any", "NVMe (Clear or Purge)",
        NistCategory.CLEAR,
        (M.NVME_SANITIZE_CRYPTO, M.NVME_SANITIZE_BLOCK, M.NVME_SANITIZE_OVERWRITE,
         M.NVME_FORMAT_CRYPTO, M.NVME_FORMAT_USER_DATA),
        "Any NVMe method the drive supports.",
        acceptance=("Device reports completion", "Sample reads show no pre-sanitize data"),
        tagline="Any NVMe firmware method.",
    ),
    Profile(
        "sata-purge", "SATA Purge",
        NistCategory.PURGE,
        (M.ATA_SANITIZE_CRYPTO, M.ATA_SANITIZE_BLOCK, M.ATA_SANITIZE_OVERWRITE,
         M.ATA_SECURITY_ERASE_ENHANCED),
        "ATA Sanitize or Enhanced Secure Erase only.",
        acceptance=("Device reports success", "Sample reads show no pre-sanitize data"),
        tagline="SATA firmware Purge only.",
    ),
]


class PolicyManager:
    def __init__(self, profiles: list[Profile] | None = None):
        self._profiles = {p.profile_id: p for p in (profiles or DEFAULT_PROFILES)}

    def profiles(self) -> list[Profile]:
        return list(self._profiles.values())

    def get(self, profile_id: str) -> Profile:
        return self._profiles[profile_id]
