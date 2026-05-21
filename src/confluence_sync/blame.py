"""Line-level blame for Confluence-sider.

Henter alle versjoner av en side, konverterer hver til markdown og
attributerer hver linje i nyeste versjon til versjonen der linjen sist
ble introdusert eller endret. Versjons-bodies caches på disk slik at
gjentatte blame-kall er øyeblikkelige.
"""

from __future__ import annotations

import difflib
import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from confluence_sync.api import ConfluenceClient
from confluence_sync.converter import storage_to_markdown

CACHE_DIR_NAME = ".gonfluence-blame"


@dataclass(frozen=True)
class Attribution:
    version: int
    author_id: str
    author_name: str
    created_at: str  # ISO 8601


@dataclass
class BlameLine:
    line: str
    attribution: Attribution


def _cache_root(docs_dir: Path) -> Path:
    return docs_dir / CACHE_DIR_NAME


def _page_cache_dir(docs_dir: Path, page_id: str) -> Path:
    return _cache_root(docs_dir) / page_id


def _version_cache_path(docs_dir: Path, page_id: str, version: int) -> Path:
    return _page_cache_dir(docs_dir, page_id) / f"v{version}.md"


def _versions_index_path(docs_dir: Path, page_id: str) -> Path:
    return _page_cache_dir(docs_dir, page_id) / "versions.json"


def _load_versions_index(docs_dir: Path, page_id: str) -> list[dict] | None:
    path = _versions_index_path(docs_dir, page_id)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def _save_versions_index(docs_dir: Path, page_id: str, versions: list[dict]) -> None:
    path = _versions_index_path(docs_dir, page_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(versions, indent=2))


def _fetch_and_cache_version(
    client: ConfluenceClient,
    docs_dir: Path,
    page_id: str,
    version_number: int,
) -> str:
    """Returner markdown for en versjon, fra cache hvis tilgjengelig."""
    cache_path = _version_cache_path(docs_dir, page_id, version_number)
    if cache_path.exists():
        return cache_path.read_text()

    detail = client.get_page_version_body(page_id, version_number)
    storage = detail.get("body", "") or ""
    markdown = storage_to_markdown(storage)

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(markdown)
    return markdown


def _resolve_author_names(
    client: ConfluenceClient, versions: list[dict]
) -> dict[str, str]:
    """Slå opp display-navn for alle unike forfattere i versjons-listen."""
    author_ids = {
        v.get("authorId") for v in versions if v.get("authorId")
    }
    names: dict[str, str] = {}
    for aid in author_ids:
        try:
            user = client.get_user(aid)
        except Exception:
            user = {}
        names[aid] = user.get("displayName") or aid
    return names


def _attribute_lines(
    new_lines: list[str],
    current: list[BlameLine],
    version_attr: Attribution,
) -> list[BlameLine]:
    """Reattribuer linjer mellom to versjoner via SequenceMatcher.

    Matchede linjer beholder eksisterende attribusjon. Nye eller endrede
    linjer tilskrives `version_attr`.
    """
    if not current:
        return [BlameLine(line=line, attribution=version_attr) for line in new_lines]

    old_lines = [bl.line for bl in current]
    matcher = difflib.SequenceMatcher(a=old_lines, b=new_lines, autojunk=False)
    next_state: list[BlameLine] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            next_state.extend(current[i1:i2])
        elif tag in ("replace", "insert"):
            for j in range(j1, j2):
                next_state.append(BlameLine(line=new_lines[j], attribution=version_attr))
        # 'delete' → linje fjernet, hopp over
    return next_state


def compute_blame(
    page_id: str,
    client: ConfluenceClient,
    docs_dir: Path,
    progress_callback: Callable[[int, int], None] | None = None,
) -> list[BlameLine]:
    """Beregn line-level blame for nyeste versjon av en side.

    Henter alle versjoner (cacher bodies), differ påfølgende versjoner og
    tilskriver hver linje til versjonen der den sist ble introdusert/endret.
    """
    versions = client.get_page_versions(page_id)
    if not versions:
        return []

    # API returnerer nyeste først; vi vil eldste først for å spille av historikken.
    versions_sorted = sorted(versions, key=lambda v: int(v.get("number", 0)))

    _save_versions_index(docs_dir, page_id, versions_sorted)

    author_names = _resolve_author_names(client, versions_sorted)

    current: list[BlameLine] = []
    total = len(versions_sorted)
    for idx, v in enumerate(versions_sorted, start=1):
        number = int(v.get("number", 0))
        author_id = v.get("authorId") or ""
        attr = Attribution(
            version=number,
            author_id=author_id,
            author_name=author_names.get(author_id, author_id or "ukjent"),
            created_at=v.get("createdAt", ""),
        )
        markdown = _fetch_and_cache_version(client, docs_dir, page_id, number)
        new_lines = markdown.splitlines()
        current = _attribute_lines(new_lines, current, attr)

        if progress_callback is not None:
            progress_callback(idx, total)

    return current


def author_summary(blame: list[BlameLine]) -> list[dict]:
    """Returner kontribusjons-statistikk per forfatter, sortert synkende."""
    counts: dict[str, dict] = {}
    for bl in blame:
        key = bl.attribution.author_id or bl.attribution.author_name
        entry = counts.setdefault(
            key,
            {
                "author_id": bl.attribution.author_id,
                "author_name": bl.attribution.author_name,
                "lines": 0,
                "latest_at": "",
            },
        )
        entry["lines"] += 1
        if bl.attribution.created_at > entry["latest_at"]:
            entry["latest_at"] = bl.attribution.created_at

    return sorted(counts.values(), key=lambda e: e["lines"], reverse=True)


def filter_since(blame: list[BlameLine], since_iso: str) -> list[BlameLine]:
    """Behold kun linjer med attribusjon på eller etter `since_iso` (YYYY-MM-DD eller full ISO)."""
    return [bl for bl in blame if bl.attribution.created_at >= since_iso]


def clear_cache(docs_dir: Path, page_id: str | None = None) -> int:
    """Slett blame-cache for en side, eller hele cachen. Returnerer antall slettede filer."""
    import shutil

    if page_id is not None:
        target = _page_cache_dir(docs_dir, page_id)
    else:
        target = _cache_root(docs_dir)

    if not target.exists():
        return 0
    count = sum(1 for _ in target.rglob("*") if _.is_file())
    shutil.rmtree(target)
    return count
