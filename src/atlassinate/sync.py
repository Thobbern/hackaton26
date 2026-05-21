import hashlib
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

import requests as req_lib

from atlassinate.api import ConfluenceClient
from atlassinate.converter import markdown_to_storage, storage_to_markdown
from atlassinate.frontmatter import read_frontmatter, write_frontmatter
from atlassinate.models import FileStatus, PageMeta, SyncState
from atlassinate.tree import build_page_tree, build_file_path


def _content_hash(text: str) -> str:
    normalized = text.strip().replace('\r\n', '\n')
    return hashlib.sha256(normalized.encode()).hexdigest()


def _traverse_tree(
    nodes: list[dict],
    parent_path: Path,
    space_key: str,
    state: SyncState,
    count: int,
    progress_callback: Callable[[str], None] | None = None,
) -> int:
    """Recursively traverse the page tree, writing markdown files and updating sync state."""
    for node in nodes:
        page = node["page"]
        children = node["children"]
        has_children = len(children) > 0

        filepath = build_file_path(page, parent_path, has_children)

        storage_value = page["body"]["storage"]["value"]
        markdown_body = storage_to_markdown(storage_value)

        content_hash = _content_hash(markdown_body)

        meta = PageMeta(
            confluence_id=page["id"],
            space_key=space_key,
            title=page["title"],
            version=page["version"]["number"],
            parent_id=page.get("parentId"),
            last_synced=datetime.now(timezone.utc).isoformat(),
            content_hash=content_hash,
        )

        write_frontmatter(filepath, meta, markdown_body)

        state.pages[page["id"]] = meta.to_dict()
        count += 1

        if progress_callback is not None:
            progress_callback(page["title"])

        if has_children:
            child_parent_path = filepath.parent
            count = _traverse_tree(children, child_parent_path, space_key, state, count, progress_callback)

    return count


def pull_space(space_key: str, output_dir: Path, client: ConfluenceClient, page_id: str | None = None, progress_callback: Callable[[str], None] | None = None) -> int:
    """Pull all pages from a Confluence space and save as local Markdown files.

    If page_id is given, only that page and its children are synced.
    Returns the number of pages synced.
    """
    if page_id is not None:
        root_page = client.get_page(page_id)
        children = client.get_page_children(page_id)
        pages = [root_page] + children
    else:
        pages = client.get_space_pages(space_key)
    tree = build_page_tree(pages)

    state = SyncState(
        instance_url=client.base_url,
        space_key=space_key,
        last_full_sync=datetime.now(timezone.utc).isoformat(),
    )

    count = _traverse_tree(tree["roots"], output_dir, space_key, state, 0, progress_callback)

    state.save(output_dir)
    return count


def push_changes(
    output_dir: Path,
    client: ConfluenceClient,
    files: list[str] | None = None,
    dry_run: bool = False,
) -> list[dict]:
    """Push local Markdown changes back to Confluence.

    Returns a list of dicts with keys: file, title, status.
    Status is one of: "pushed", "skipped", "dry_run".
    """
    state = SyncState.load(output_dir)

    if files is not None:
        filepaths = [Path(f) for f in files]
    else:
        filepaths = list(output_dir.rglob("*.md"))

    results = []

    for filepath in filepaths:
        if not filepath.exists():
            continue

        try:
            meta, body = read_frontmatter(filepath)
        except (KeyError, ValueError):
            continue

        content_hash = _content_hash(body)

        if content_hash == meta.content_hash:
            results.append({"file": str(filepath), "title": meta.title, "status": "skipped"})
            continue

        storage_body = markdown_to_storage(body)

        if dry_run:
            results.append({"file": str(filepath), "title": meta.title, "status": "dry_run"})
            continue

        try:
            client.update_page(meta.confluence_id, meta.title, storage_body, meta.version + 1)
        except req_lib.HTTPError as e:
            if e.response is not None and e.response.status_code == 409:
                results.append({"file": str(filepath), "title": meta.title, "status": "conflict"})
                continue
            raise

        new_version = meta.version + 1
        updated_meta = PageMeta(
            confluence_id=meta.confluence_id,
            space_key=meta.space_key,
            title=meta.title,
            version=new_version,
            parent_id=meta.parent_id,
            last_synced=datetime.now(timezone.utc).isoformat(),
            content_hash=content_hash,
        )

        write_frontmatter(filepath, updated_meta, body)

        state.pages[meta.confluence_id] = updated_meta.to_dict()

        results.append({"file": str(filepath), "title": meta.title, "status": "pushed"})

    state.save(output_dir)
    return results


def get_status(output_dir: Path, client: ConfluenceClient | None = None) -> list[dict]:
    """Return sync status for all Markdown files in output_dir.

    Each entry is a dict with keys: file, title, status (FileStatus).
    """
    state = SyncState.load(output_dir)

    results = []

    for filepath in sorted(output_dir.rglob("*.md")):
        try:
            meta, body = read_frontmatter(filepath)
        except Exception:
            # Skip files that don't have valid frontmatter
            continue

        local_hash = _content_hash(body)
        modified_local = local_hash != meta.content_hash

        modified_remote = False
        if client is not None:
            try:
                remote_page = client.get_page(meta.confluence_id)
                remote_version = remote_page["version"]["number"]
                modified_remote = remote_version != meta.version
            except Exception:
                # If the remote fetch fails, treat as unknown (not modified)
                pass

        if modified_local and modified_remote:
            file_status = FileStatus.CONFLICT
        elif modified_local:
            file_status = FileStatus.MODIFIED_LOCAL
        elif modified_remote:
            file_status = FileStatus.MODIFIED_REMOTE
        else:
            file_status = FileStatus.UNCHANGED

        results.append({"file": str(filepath), "title": meta.title, "status": file_status})

    return results
