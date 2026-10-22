"""Sample reads before and after an erase (spec 8.2).

Reads a few blocks spread over the drive and compares them. This shows the
old data is gone from the sampled places; it never proves every physical
block was erased, and reports say so.
"""

from __future__ import annotations

import hashlib
import os

BLOCK = 4096


def sample_offsets(size: int, n: int = 64, block: int = BLOCK) -> list[int]:
    """n block-aligned offsets spread evenly, always including the first and last block."""
    last = (size // block - 1) * block
    if last < 0:
        return []
    if n <= 1 or last == 0:
        return [0]
    step = last / (n - 1)
    return sorted({int(i * step) // block * block for i in range(n)})


def _drop_cache(fd: int) -> None:
    if hasattr(os, "posix_fadvise"):
        try:
            os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)
        except OSError:
            pass


def read_samples(path: str, size: int, n: int = 64, block: int = BLOCK) -> dict:
    """{"offsets": {offset: {"sha256", "uniform"}}, "error": str|None}.

    uniform = the byte value when the whole block is one repeated byte, else None.
    """
    out: dict = {"offsets": {}, "error": None}
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_BINARY", 0))
    except OSError as e:
        out["error"] = f"open failed: {e}"
        return out
    try:
        _drop_cache(fd)
        for off in sample_offsets(size, n, block):
            try:
                os.lseek(fd, off, os.SEEK_SET)
                data = os.read(fd, block)
            except OSError as e:
                out["offsets"][off] = {"sha256": None, "uniform": None, "error": str(e)}
                continue
            uni = data[0] if data and data.count(data[0]) == len(data) else None
            out["offsets"][off] = {"sha256": hashlib.sha256(data).hexdigest(), "uniform": uni}
    finally:
        os.close(fd)
    return out


def compare(before: dict | None, after: dict | None) -> dict:
    """Turn two sample sets into one post-check."""
    name = "Sample reads before vs after"
    if not before or before.get("error") or not after or after.get("error"):
        why = (before or {}).get("error") or (after or {}).get("error") or "no samples"
        return {"check": name, "passed": None, "shows": f"Samples could not be read ({why})."}
    informative = [o for o, s in before["offsets"].items()
                   if s.get("sha256") and s.get("uniform") is None]
    if not informative:
        return {"check": name, "passed": None,
                "shows": "The sampled blocks held no data before the erase (all one byte), so "
                         "sampling cannot show a change."}
    unchanged = [o for o in informative
                 if after["offsets"].get(o, {}).get("sha256") == before["offsets"][o]["sha256"]]
    unreadable = [o for o in informative if after["offsets"].get(o, {}).get("sha256") is None]
    if unchanged:
        return {"check": name, "passed": False,
                "shows": f"{len(unchanged)} of {len(informative)} sampled blocks still hold the "
                         f"old data (first at byte {unchanged[0]:,}).",
                "unchanged_offsets": unchanged[:20]}
    note = f" {len(unreadable)} blocks could not be read after the erase." if unreadable else ""
    return {"check": name, "passed": True,
            "shows": f"All {len(informative)} sampled blocks that held data before now differ."
                     + note + " Does not prove every physical block was erased."}
