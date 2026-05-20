import hashlib
from datetime import datetime, timezone
from pathlib import Path

from confluence_sync.api import ConfluenceClient
from confluence_sync.converter import markdown_to_storage, storage_to_markdown
from confluence_sync.frontmatter import read_frontmatter, write_frontmatter
from confluence_sync.models import FileStatus, PageMeta, SyncState
from confluence_sync.tree import build_page_tree, build_file_path


def _traverse_tree(
    nodes: list[dict],
    parent_path: Path,
    space_key: str,
    state: SyncState,
    count: int,
) -> int:
    """Recursively traverse the page tree, writing markdown files and updating sync state."""
    for node in nodes:
        page = node["page"]
        children = node["children"]
        has_children = len(children) > 0

        filepath = build_file_path(page, parent_path, has_children)

        storage_value = page["body"]["storage"]["value"]
        markdown_body = storage_to_markdown(storage_value)

        content_hash = hashlib.sha256(markdown_body.encode()).hexdigest()

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

        if has_children:
            child_parent_path = filepath.parent
            count = _traverse_tree(children, child_parent_path, space_key, state, count)

    return count


def pull_space(space_key: str, output_dir: Path, client: ConfluenceClient) -> int:
    """Pull all pages from a Confluence space and save as local Markdown files.

    Returns the number of pages synced.
    """
    pages = client.get_space_pages(space_key)
    tree = build_page_tree(pages)

    state = SyncState(
        instance_url=client.base_url,
        space_key=space_key,
        last_full_sync=datetime.now(timezone.utc).isoformat(),
    )

    count = _traverse_tree(tree["roots"], output_dir, space_key, state, 0)

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

        meta, body = read_frontmatter(filepath)

        content_hash = hashlib.sha256(body.encode()).hexdigest()

        if content_hash == meta.content_hash:
            results.append({"file": str(filepath), "title": meta.title, "status": "skipped"})
            continue

        storage_body = markdown_to_storage(body)

        if dry_run:
            results.append({"file": str(filepath), "title": meta.title, "status": "dry_run"})
            continue

        client.update_page(meta.confluence_id, meta.title, storage_body, meta.version + 1)

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

        local_hash = hashlib.sha256(body.encode()).hexdigest()
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
