"""Edit-modus for atlassinate.

En "edit" er en arbeidskopi av en synket Confluence-side under
`~/.atlassinate/gonfluence/.edits/<page_id>/`. Kopien har samme frontmatter
som mirror-fila, der `version` og `content_hash` representerer base-en
edit-en startet fra. Når brukeren `submit`-er sammenligner vi remote-versjon
mot base for konfliktdeteksjon.

`rebase` henter siste remote-versjon og oppdaterer base. Hvis arbeidskopien
har lokale endringer beholdes brukerens body (manuell 3-veis-merge er ikke
i scope) — base oppdateres slik at neste submit overskriver remote.
"""

import hashlib
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from atlassinate.api import ConfluenceClient
from atlassinate.converter import markdown_to_storage, storage_to_markdown
from atlassinate.frontmatter import read_frontmatter, write_frontmatter
from atlassinate.models import PageMeta, SyncState
from atlassinate.paths import (
    edits_archive,
    edits_path,
    edits_root,
    ensure_dir,
    gonfluence_root,
    mirror_path,
)


def _content_hash(text: str) -> str:
    normalized = text.strip().replace("\r\n", "\n")
    return hashlib.sha256(normalized.encode()).hexdigest()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class EditEntry:
    page_id: str
    edit_dir: Path
    filepath: Path
    meta: PageMeta
    body: str
    modified: bool

    @property
    def title(self) -> str:
        return self.meta.title

    @property
    def base_version(self) -> int:
        return self.meta.version


def find_mirror_file(page_id: str) -> Path | None:
    """Finn mirror-fila for en page-id. Søker under `~/.atlassinate/gonfluence/`,
    men hopper over `.edits/`-undertreet."""
    root = gonfluence_root()
    if not root.exists():
        return None
    edits = edits_root()
    for md in root.rglob("*.md"):
        try:
            md.relative_to(edits)
            continue
        except ValueError:
            pass
        try:
            meta, _ = read_frontmatter(md)
        except Exception:
            continue
        if meta.confluence_id == page_id:
            return md
    return None


def _existing_edit_file(edit_dir: Path) -> Path | None:
    if not edit_dir.exists():
        return None
    for f in sorted(edit_dir.glob("*.md")):
        return f
    return None


def start_edit(page_id: str) -> Path:
    """Start (eller gjenoppta) en edit. Returnerer stien til arbeidsfila."""
    edit_dir = edits_path(page_id)
    existing = _existing_edit_file(edit_dir)
    if existing is not None:
        return existing

    mirror_file = find_mirror_file(page_id)
    if mirror_file is None:
        raise FileNotFoundError(
            f"Side {page_id} ikke funnet i mirror. Kjør `gonfluence sync --space <KEY>` først."
        )

    ensure_dir(edit_dir)
    target = edit_dir / mirror_file.name
    shutil.copyfile(mirror_file, target)
    return target


def load_edit(page_id: str) -> EditEntry:
    edit_dir = edits_path(page_id)
    md_file = _existing_edit_file(edit_dir)
    if md_file is None:
        raise FileNotFoundError(f"Ingen pågående edit for side {page_id}.")
    meta, body = read_frontmatter(md_file)
    current_hash = _content_hash(body)
    return EditEntry(
        page_id=page_id,
        edit_dir=edit_dir,
        filepath=md_file,
        meta=meta,
        body=body,
        modified=current_hash != meta.content_hash,
    )


def list_edits() -> list[EditEntry]:
    root = edits_root()
    if not root.exists():
        return []
    entries: list[EditEntry] = []
    for sub in sorted(root.iterdir()):
        if not sub.is_dir() or sub.name.startswith("."):
            continue
        try:
            entries.append(load_edit(sub.name))
        except Exception:
            continue
    return entries


def discard_edit(page_id: str) -> Path:
    """Arkiver edit-mappa til `.edits/.archive/<timestamp>_<page_id>/`."""
    edit_dir = edits_path(page_id)
    if not edit_dir.exists():
        raise FileNotFoundError(f"Ingen pågående edit for side {page_id}.")
    archive_root = ensure_dir(edits_archive())
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = archive_root / f"{stamp}_{page_id}"
    edit_dir.rename(target)
    return target


def _sync_mirror_after_submit(
    page_id: str, new_meta: PageMeta, body: str
) -> Path | None:
    """Oppdater mirror-fila og sync-state etter en vellykket submit."""
    mirror_file = find_mirror_file(page_id)
    if mirror_file is not None:
        write_frontmatter(mirror_file, new_meta, body)

    space_dir = mirror_path(new_meta.space_key)
    if SyncState.state_file_present(space_dir):
        state = SyncState.load(space_dir)
        state.pages[page_id] = new_meta.to_dict()
        state.save(space_dir)

    return mirror_file


def submit_edit(page_id: str, client: ConfluenceClient) -> dict:
    """Push edit-en til Confluence. Returnerer status-dict.

    Statuser:
      - "unchanged"  → ingen lokale endringer, ingen-op
      - "conflict"   → remote-versjon != base; bruker må `rebase`
      - "submitted"  → pushet til Confluence, mirror oppdatert, edit arkivert
    """
    entry = load_edit(page_id)
    if not entry.modified:
        return {
            "page_id": page_id,
            "title": entry.title,
            "status": "unchanged",
        }

    remote = client.get_page(page_id)
    remote_version = remote["version"]["number"]
    if remote_version != entry.base_version:
        return {
            "page_id": page_id,
            "title": entry.title,
            "status": "conflict",
            "base_version": entry.base_version,
            "remote_version": remote_version,
        }

    storage_body = markdown_to_storage(entry.body)
    new_version = entry.base_version + 1
    client.update_page(page_id, entry.title, storage_body, new_version)

    new_meta = PageMeta(
        confluence_id=entry.meta.confluence_id,
        space_key=entry.meta.space_key,
        title=entry.title,
        version=new_version,
        parent_id=entry.meta.parent_id,
        last_synced=_now_iso(),
        content_hash=_content_hash(entry.body),
    )

    _sync_mirror_after_submit(page_id, new_meta, entry.body)
    archived_at = discard_edit(page_id)

    return {
        "page_id": page_id,
        "title": entry.title,
        "status": "submitted",
        "new_version": new_version,
        "archived_at": str(archived_at),
    }


def rebase_edit(page_id: str, client: ConfluenceClient) -> dict:
    """Hent siste remote-versjon og oppdater edit-ens base.

    Statuser:
      - "noop"                       → remote-versjon == base, ingenting å gjøre
      - "rebased_clean"              → ingen lokale endringer; edit erstattes med remote
      - "rebased_with_local_changes" → lokale endringer beholdt, base oppdatert
                                       til remote (neste submit overskriver remote)
    """
    entry = load_edit(page_id)
    remote = client.get_page(page_id)
    remote_version = remote["version"]["number"]

    if remote_version == entry.base_version:
        return {"page_id": page_id, "title": entry.title, "status": "noop"}

    storage_value = remote["body"]["storage"]["value"]
    remote_body = storage_to_markdown(storage_value)
    remote_title = remote.get("title", entry.title)

    new_meta = PageMeta(
        confluence_id=entry.meta.confluence_id,
        space_key=entry.meta.space_key,
        title=remote_title,
        version=remote_version,
        parent_id=remote.get("parentId", entry.meta.parent_id),
        last_synced=_now_iso(),
        content_hash=_content_hash(remote_body),
    )

    if entry.modified:
        # Behold brukerens body men oppdater base til remote. Frontmatter-hash
        # tilsvarer remote-body, så `modified` forblir True og neste submit
        # vil pushe brukerens endringer (og overskrive remote).
        write_frontmatter(entry.filepath, new_meta, entry.body)
        status = "rebased_with_local_changes"
    else:
        write_frontmatter(entry.filepath, new_meta, remote_body)
        status = "rebased_clean"

    return {
        "page_id": page_id,
        "title": new_meta.title,
        "status": status,
        "new_base_version": remote_version,
        "previous_base_version": entry.base_version,
    }
