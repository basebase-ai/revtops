"""Pytest configuration. Ensures backend root is on sys.path for imports like api.*, agents.*, etc."""
import sys
from pathlib import Path

_backend: Path = Path(__file__).resolve().parent
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))
