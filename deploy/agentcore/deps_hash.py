#!/usr/bin/env python3
"""Hash of the locked runtime dependencies, from `uv export` output on stdin.

Used twice: by the Dockerfile (written to /opt/magi/deps.sha256) and by push_code.py (stored in the code
bundle's manifest). The bootstrap refuses a bundle whose hash differs from the image's. Only the sorted
`name==version` pairs count, so the comments, markers and layout of different uv versions do not matter.
"""

from __future__ import annotations

import hashlib
import re
import sys

PIN = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)(?:\[[^\]]*\])?==([^\s;]+)")


def digest(text: str) -> str:
    pins = sorted({f"{m.group(1).lower().replace('_', '-')}=={m.group(2)}" for line in text.splitlines() if (m := PIN.match(line))})
    return hashlib.sha256("\n".join(pins).encode()).hexdigest()


if __name__ == "__main__":
    print(digest(sys.stdin.read()))
