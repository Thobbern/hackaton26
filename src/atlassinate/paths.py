"""Standard plassering for atlassinate-data i hjemmemappa."""

import os
from pathlib import Path


def atlassinate_home() -> Path:
    """Rotmappa for all atlassinate-state. Overstyrbar via $ATLASSINATE_HOME."""
    env = os.environ.get("ATLASSINATE_HOME")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".atlassinate"


def gonfluence_root() -> Path:
    return atlassinate_home() / "gonfluence"


def mirror_path(space_key: str) -> Path:
    return gonfluence_root() / space_key


def edits_root() -> Path:
    return gonfluence_root() / ".edits"


def edits_path(page_id: str) -> Path:
    return edits_root() / page_id


def edits_archive() -> Path:
    return edits_root() / ".archive"


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path
