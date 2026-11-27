# -*- coding: utf-8 -*-
"""Shared shell discovery for tests that execute real POSIX scripts."""
import os
import shutil
from pathlib import Path

import pytest


def find_bash():
    """Return Git Bash on Windows, a POSIX bash elsewhere; never WSL's system32 shim."""
    candidates = []
    if os.name == "nt":
        candidates.extend((
            r"C:\Program Files\Git\bin\bash.exe",
            r"C:\Program Files\Git\usr\bin\bash.exe",
        ))
    found = shutil.which("bash")
    if found:
        candidates.append(found)
    for candidate in candidates:
        if not candidate:
            continue
        if "system32" in candidate.lower():
            continue
        if Path(candidate).exists():
            return candidate
    return None


BASH = find_bash()
needs_bash = pytest.mark.skipif(
    BASH is None,
    reason="bash (Git Bash on Windows) is required; Linux CI always has it",
)
