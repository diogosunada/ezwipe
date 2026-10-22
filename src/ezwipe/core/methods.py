"""Catalog of sanitization methods.

The NIST category for each method is PROVISIONAL. NIST SP 800-88 Rev. 2
defers per-media techniques to IEEE 2883, which still has to be reviewed.
Until then every category is marked provisional and reports say so.
A method name containing "Secure Erase" or "Sanitize" never earns a
category by itself (spec 4.4).
"""

from __future__ import annotations

from dataclasses import dataclass

from .models import MethodId, NistCategory, Protocol

POLICY_VERSION = "ezwipe-policy-0.2-provisional"


@dataclass(frozen=True)
class MethodInfo:
    id: MethodId
    name: str
    protocol: Protocol
    category: NistCategory
    category_provisional: bool
    scope: str
    verification: str
    destructive: bool = True
    implemented: bool = False
    short: str = ""          # label for drive cards and reports
    passes: tuple[str, ...] = ()   # host overwrite patterns, in order

    @property
    def label(self) -> str:
        return self.short or self.name


_CATALOG: dict[MethodId, MethodInfo] = {}


def _add(info: MethodInfo) -> None:
    _CATALOG[info.id] = info


_add(MethodInfo(
    MethodId.NVME_SANITIZE_CRYPTO, "NVMe Sanitize - Crypto Erase", Protocol.NVME,
    NistCategory.PURGE, True,
    scope="All user data in the NVM subsystem, including caches and unallocated areas, "
          "by changing the media encryption key.",
    verification="Sanitize Status log (0x81) must report success after the operation; "
                 "sample reads must not return the pre-sanitize pattern.",
    short="NVMe Crypto Erase",
))
_add(MethodInfo(
    MethodId.NVME_SANITIZE_BLOCK, "NVMe Sanitize - Block Erase", Protocol.NVME,
    NistCategory.PURGE, True,
    scope="All user data in the NVM subsystem by a low-level block erase.",
    verification="Sanitize Status log (0x81) must report success; sample reads checked.",
    short="NVMe Block Erase",
))
_add(MethodInfo(
    MethodId.NVME_SANITIZE_OVERWRITE, "NVMe Sanitize - Overwrite", Protocol.NVME,
    NistCategory.PURGE, True,
    scope="All user data in the NVM subsystem by a device-driven overwrite.",
    verification="Sanitize Status log (0x81) must report success; sample reads checked.",
    short="NVMe Sanitize Overwrite",
))
_add(MethodInfo(
    MethodId.NVME_FORMAT_USER_DATA, "NVMe Format - User Data Erase", Protocol.NVME,
    NistCategory.CLEAR, True,
    scope="Depends on FNA bits: one namespace or all namespaces. Does not promise "
          "to reach every physical location (spec 4.1).",
    verification="Command completion plus sample reads; weaker evidence than Sanitize.",
    short="NVMe Format",
))
_add(MethodInfo(
    MethodId.NVME_FORMAT_CRYPTO, "NVMe Format - Cryptographic Erase", Protocol.NVME,
    NistCategory.PURGE, True,
    scope="Depends on FNA bits: one namespace or all namespaces.",
    verification="Command completion plus sample reads.",
    short="NVMe Format (crypto)",
))
_add(MethodInfo(
    MethodId.ATA_SANITIZE_CRYPTO, "ATA Sanitize - Crypto Scramble", Protocol.ATA,
    NistCategory.PURGE, True, scope="All user data areas.",
    verification="SANITIZE STATUS EXT must report success; sample reads checked.",
    short="ATA Crypto Scramble",
))
_add(MethodInfo(
    MethodId.ATA_SANITIZE_BLOCK, "ATA Sanitize - Block Erase", Protocol.ATA,
    NistCategory.PURGE, True, scope="All user data areas.",
    verification="SANITIZE STATUS EXT must report success; sample reads checked.",
    short="ATA Block Erase",
))
_add(MethodInfo(
    MethodId.ATA_SANITIZE_OVERWRITE, "ATA Sanitize - Overwrite", Protocol.ATA,
    NistCategory.PURGE, True, scope="All user data areas.",
    verification="SANITIZE STATUS EXT must report success; sample reads checked.",
    short="ATA Sanitize Overwrite",
))
_add(MethodInfo(
    MethodId.ATA_SECURITY_ERASE, "ATA Security Erase Unit (normal)", Protocol.ATA,
    NistCategory.CLEAR, True,
    scope="User addressable area; reach of reallocated sectors is vendor defined.",
    verification="Security feature disabled again after the erase, plus sampled read-back.",
    short="ATA Secure Erase",
))
_add(MethodInfo(
    MethodId.ATA_SECURITY_ERASE_ENHANCED, "ATA Security Erase Unit (enhanced)", Protocol.ATA,
    NistCategory.PURGE, True,
    scope="Vendor defined; includes reallocated areas on many drives.",
    verification="Security feature disabled again after the erase, plus sampled read-back.",
    short="ATA Enhanced Secure Erase",
))
_OVERWRITE_SCOPE = ("Only LBAs visible to the host. Remapped sectors, DCO and over-provisioned "
                    "flash are NOT covered (spec 4.3). A host protected area (HPA) is opened "
                    "first when the profile allows it.")
_add(MethodInfo(
    MethodId.OVERWRITE_ADDRESSABLE, "Overwrite - 1 random pass", Protocol.UNKNOWN,
    NistCategory.CLEAR, True, scope=_OVERWRITE_SCOPE,
    verification="Read-back of the written pattern (full or sampled, as the profile sets).",
    short="Random overwrite (1 pass)", passes=("random",),
))
_add(MethodInfo(
    MethodId.OVERWRITE_ZERO, "Overwrite - 1 zero pass", Protocol.UNKNOWN,
    NistCategory.CLEAR, True, scope=_OVERWRITE_SCOPE,
    verification="Read-back of the written pattern (full or sampled, as the profile sets).",
    short="Zero fill (1 pass)", passes=("zero",),
))
_add(MethodInfo(
    MethodId.OVERWRITE_DOD_3PASS, "Overwrite - DoD 5220.22-M style, 3 passes", Protocol.UNKNOWN,
    NistCategory.CLEAR, True,
    scope=_OVERWRITE_SCOPE + " Legacy standard: three passes add time, not assurance, "
          "on modern drives.",
    verification="Read-back of the last pass (full or sampled, as the profile sets).",
    short="DoD 3-pass", passes=("zero", "one", "random"),
))


def method_info(method: MethodId) -> MethodInfo:
    return _CATALOG[method]


def all_methods() -> list[MethodInfo]:
    return list(_CATALOG.values())
