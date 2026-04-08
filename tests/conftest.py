"""Test helpers for Production Ops Lab."""

from __future__ import annotations

import sys
from pathlib import Path


ENV_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = ENV_ROOT.parent

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
