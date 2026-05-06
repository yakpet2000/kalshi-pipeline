"""Pytest configuration: makes the project root importable.

Allows tests to import `simulator.<module>` without an installed package.
"""
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
