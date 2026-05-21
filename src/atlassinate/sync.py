import hashlib
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from atlassinate.api import ConfluenceClient
from atlassinate.converter import storage_to_markdown
from atlassinate.frontmatter import read_frontmatter, write_frontmatter
from atlassinate.models import PageMeta, SyncState
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


def mirror_space(
    space_key: str,
    output_dir: Path,
    client: ConfluenceClient,
    page_id: str | None = None,
    incremental: bool = True,
    progress_callback: Callable[[str, str], None] | None = None,
) -> dict:
    """Speil et Confluence-space lokalt som Markdown (enveis, server → lokal).

    Inkrementell modus (default): hopper over sider hvor remote `version`
    matcher lagret state. Sider som finnes lokalt men ikke remote, fjernes.

    Hvis page_id er gitt synkes kun den siden og dens barn, og deletion-
    tracking er deaktivert (siden vi ikke har hele space-bildet).

    Returnerer dict med statistikk: pulled, skipped, removed, total.

    Progress-callback kalles med (title, action) hvor action er "pulled",
    "skipped" eller "removed".
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    prior_state = SyncState.load(output_dir) if incremental else SyncState()
    prior_versions = {pid: meta.get("version") for pid, meta in prior_state.pages.items()}
    prior_pages_to_remove = set(prior_state.pages.keys())

    new_state = SyncState(
        instance_url=client.base_url,
        space_key=space_key,
        last_full_sync=datetime.now(timezone.utc).isoformat(),
        pages=dict(prior_state.pages),
    )

    if page_id is not None:
        root_page = client.get_page(page_id)
        children = client.get_page_children(page_id)
        full_pages = [root_page] + children
        summaries = full_pages
        track_deletions = False
    else:
        summaries = client.get_space_page_summaries(space_key)
        full_pages = None
        track_deletions = True

    pulled = 0
    skipped = 0

    pages_to_fetch: list[dict] = []
    for summary in summaries:
        pid = summary["id"]
        prior_pages_to_remove.discard(pid)
        remote_version = (summary.get("version") or {}).get("number")
        if (
            incremental
            and remote_version is not None
            and prior_versions.get(pid) == remote_version
        ):
            skipped += 1
            if progress_callback is not None:
                skipped_title = summary.get("title", pid)
                progress_callback(skipped_title, "skipped")
            continue
        pages_to_fetch.append(summary)

    if full_pages is not None:
        fetched_pages = [p for p in full_pages if p["id"] in {s["id"] for s in pages_to_fetch}]
    else:
        fetched_pages = [client.get_page(s["id"]) for s in pages_to_fetch]

    tree = build_page_tree(fetched_pages)
    pulled = _traverse_tree(
        tree["roots"],
        output_dir,
        space_key,
        new_state,
        0,
        lambda title: progress_callback(title, "pulled") if progress_callback else None,
    )

    removed = 0
    if track_deletions:
        for pid in prior_pages_to_remove:
            meta = prior_state.pages.get(pid, {})
            removed_title = meta.get("title", pid)
            file_rel = _find_local_file(output_dir, pid)
            if file_rel is not None and file_rel.exists():
                file_rel.unlink()
            new_state.pages.pop(pid, None)
            removed += 1
            if progress_callback is not None:
                progress_callback(removed_title, "removed")

    new_state.save(output_dir)

    return {
        "pulled": pulled,
        "skipped": skipped,
        "removed": removed,
        "total": pulled + skipped,
    }


def _find_local_file(output_dir: Path, page_id: str) -> Path | None:
    """Finn lokal markdown-fil som matcher en confluence_id i frontmatter."""
    for md in output_dir.rglob("*.md"):
        try:
            meta, _ = read_frontmatter(md)
        except Exception:
            continue
        if meta.confluence_id == page_id:
            return md
    return None


