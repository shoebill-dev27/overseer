"""Shared pytest configuration — add the agent root to the import path."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
