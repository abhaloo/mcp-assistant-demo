"""Repo-anchored paths. Kills the sys.path.insert copies across scripts/."""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
