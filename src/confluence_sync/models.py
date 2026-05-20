from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime
from pathlib import Path
import json


class FileStatus(Enum):
    UNCHANGED = "unchanged"
    MODIFIED_LOCAL = "modified_local"
    MODIFIED_REMOTE = "modified_remote"
    CONFLICT = "conflict"
    NEW_LOCAL = "new_local"
    DELETED_LOCAL = "deleted_local"


@dataclass
class PageMeta:
    confluence_id: str
    space_key: str
    title: str
    version: int
    parent_id: str | None = None
    last_synced: str = ""
    content_hash: str = ""

    def to_dict(self) -> dict:
        return {
            "confluence_id": self.confluence_id,
            "space_key": self.space_key,
            "title": self.title,
            "version": self.version,
            "parent_id": self.parent_id,
            "last_synced": self.last_synced,
            "content_hash": self.content_hash,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "PageMeta":
        return cls(
            confluence_id=data["confluence_id"],
            space_key=data["space_key"],
            title=data["title"],
            version=data["version"],
            parent_id=data.get("parent_id"),
            last_synced=data.get("last_synced", ""),
            content_hash=data.get("content_hash", ""),
        )


@dataclass
class SyncState:
    version: int = 1
    instance_url: str = ""
    space_key: str = ""
    last_full_sync: str = ""
    pages: dict[str, dict] = field(default_factory=dict)

    def save(self, path: Path) -> None:
        data = {
            "version": self.version,
            "instance_url": self.instance_url,
            "space_key": self.space_key,
            "last_full_sync": self.last_full_sync,
            "pages": self.pages,
        }
        sync_file = path / ".confluence-sync.json"
        sync_file.write_text(json.dumps(data, indent=2))

    @classmethod
    def load(cls, path: Path) -> "SyncState":
        sync_file = path / ".confluence-sync.json"
        if not sync_file.exists():
            return cls()
        data = json.loads(sync_file.read_text())
        return cls(
            version=data.get("version", 1),
            instance_url=data.get("instance_url", ""),
            space_key=data.get("space_key", ""),
            last_full_sync=data.get("last_full_sync", ""),
            pages=data.get("pages", {}),
        )
