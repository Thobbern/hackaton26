"""Tester for atlassinate.edit (edit/submit/rebase-flyten)."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from atlassinate import edit
from atlassinate.frontmatter import read_frontmatter, write_frontmatter
from atlassinate.models import PageMeta, SyncState


@pytest.fixture
def atlassinate_root(tmp_path, monkeypatch):
    monkeypatch.setenv("ATLASSINATE_HOME", str(tmp_path))
    return tmp_path


def _seed_mirror(space: str, page_id: str, title: str, version: int, body: str) -> Path:
    from atlassinate.paths import mirror_path

    mirror = mirror_path(space)
    mirror.mkdir(parents=True, exist_ok=True)

    content_hash = edit._content_hash(body)
    meta = PageMeta(
        confluence_id=page_id,
        space_key=space,
        title=title,
        version=version,
        parent_id=None,
        last_synced="2026-01-01T00:00:00+00:00",
        content_hash=content_hash,
    )
    filepath = mirror / f"{title.lower()}.md"
    write_frontmatter(filepath, meta, body)

    state = SyncState(
        instance_url="https://x.atlassian.net/wiki/api/v2",
        space_key=space,
        last_full_sync="2026-01-01T00:00:00+00:00",
        pages={page_id: meta.to_dict()},
    )
    state.save(mirror)
    return filepath


def _full_remote(page_id: str, title: str, version: int, body_html: str, parent_id=None) -> dict:
    return {
        "id": page_id,
        "title": title,
        "parentId": parent_id,
        "version": {"number": version},
        "body": {"storage": {"value": body_html}},
    }


def test_start_edit_copies_mirror_file(atlassinate_root):
    _seed_mirror("DEV", "1", "Alpha", 3, "Hello")
    target = edit.start_edit("1")

    assert target.exists()
    assert target.name == "alpha.md"
    assert "Hello" in target.read_text()


def test_start_edit_idempotent_returns_existing(atlassinate_root):
    _seed_mirror("DEV", "1", "Alpha", 3, "Hello")
    first = edit.start_edit("1")
    first.write_text(first.read_text().replace("Hello", "Modified"))

    second = edit.start_edit("1")
    assert second == first
    assert "Modified" in second.read_text()


def test_start_edit_unknown_page_raises(atlassinate_root):
    with pytest.raises(FileNotFoundError):
        edit.start_edit("999")


def test_load_edit_detects_modification(atlassinate_root):
    _seed_mirror("DEV", "1", "Alpha", 3, "Hello")
    target = edit.start_edit("1")

    entry = edit.load_edit("1")
    assert entry.modified is False
    assert entry.base_version == 3

    meta, _ = read_frontmatter(target)
    write_frontmatter(target, meta, "New body")

    entry = edit.load_edit("1")
    assert entry.modified is True
    assert entry.body == "New body"


def test_list_edits(atlassinate_root):
    _seed_mirror("DEV", "1", "Alpha", 1, "a")
    _seed_mirror("DEV", "2", "Beta", 1, "b")
    edit.start_edit("1")
    edit.start_edit("2")

    entries = edit.list_edits()
    page_ids = {e.page_id for e in entries}
    assert page_ids == {"1", "2"}


def test_discard_edit_moves_to_archive(atlassinate_root):
    _seed_mirror("DEV", "1", "Alpha", 1, "a")
    edit_file = edit.start_edit("1")
    assert edit_file.exists()

    target = edit.discard_edit("1")
    assert target.exists()
    assert not edit_file.exists()
    assert "1" in target.name


def test_discard_edit_unknown_raises(atlassinate_root):
    with pytest.raises(FileNotFoundError):
        edit.discard_edit("nope")


def test_submit_unchanged_is_noop(atlassinate_root):
    _seed_mirror("DEV", "1", "Alpha", 3, "Hello")
    edit.start_edit("1")
    client = MagicMock()

    result = edit.submit_edit("1", client)
    assert result["status"] == "unchanged"
    client.update_page.assert_not_called()


def test_submit_pushes_changes_and_archives(atlassinate_root):
    mirror_file = _seed_mirror("DEV", "1", "Alpha", 3, "Hello")
    target = edit.start_edit("1")
    meta, _ = read_frontmatter(target)
    write_frontmatter(target, meta, "Updated body")

    client = MagicMock()
    client.update_page.return_value = {"id": "1"}
    client.get_page.return_value = _full_remote("1", "Alpha", 3, "<p>Hello</p>")

    result = edit.submit_edit("1", client)

    assert result["status"] == "submitted"
    assert result["new_version"] == 4
    client.update_page.assert_called_once()
    args, _ = client.update_page.call_args
    assert args[0] == "1"
    assert args[3] == 4

    new_meta, new_body = read_frontmatter(mirror_file)
    assert new_meta.version == 4
    assert "Updated body" in new_body

    state = SyncState.load(mirror_file.parent)
    assert state.pages["1"]["version"] == 4

    # Edit-mappa skal være tom, og en archive-entry skal eksistere
    from atlassinate.paths import edits_archive, edits_path

    assert not edits_path("1").exists()
    archives = list(edits_archive().iterdir())
    assert any("1" in a.name for a in archives)


def test_submit_detects_conflict(atlassinate_root):
    _seed_mirror("DEV", "1", "Alpha", 3, "Hello")
    target = edit.start_edit("1")
    meta, _ = read_frontmatter(target)
    write_frontmatter(target, meta, "Updated body")

    client = MagicMock()
    # Remote har gått foran til v5
    client.get_page.return_value = _full_remote("1", "Alpha", 5, "<p>Hello</p>")

    result = edit.submit_edit("1", client)
    assert result["status"] == "conflict"
    assert result["base_version"] == 3
    assert result["remote_version"] == 5
    client.update_page.assert_not_called()


def test_rebase_noop_when_remote_unchanged(atlassinate_root):
    _seed_mirror("DEV", "1", "Alpha", 3, "Hello")
    edit.start_edit("1")

    client = MagicMock()
    client.get_page.return_value = _full_remote("1", "Alpha", 3, "<p>Hello</p>")

    result = edit.rebase_edit("1", client)
    assert result["status"] == "noop"


def test_rebase_clean_replaces_with_remote(atlassinate_root):
    _seed_mirror("DEV", "1", "Alpha", 3, "Hello")
    target = edit.start_edit("1")

    client = MagicMock()
    client.get_page.return_value = _full_remote("1", "Alpha", 5, "<p>Remote v5</p>")

    result = edit.rebase_edit("1", client)
    assert result["status"] == "rebased_clean"
    assert result["new_base_version"] == 5

    meta, body = read_frontmatter(target)
    assert meta.version == 5
    assert "Remote v5" in body


def test_rebase_keeps_local_changes(atlassinate_root):
    _seed_mirror("DEV", "1", "Alpha", 3, "Hello")
    target = edit.start_edit("1")
    meta, _ = read_frontmatter(target)
    write_frontmatter(target, meta, "My local edit")

    client = MagicMock()
    client.get_page.return_value = _full_remote("1", "Alpha", 5, "<p>Remote v5</p>")

    result = edit.rebase_edit("1", client)
    assert result["status"] == "rebased_with_local_changes"

    new_meta, body = read_frontmatter(target)
    assert new_meta.version == 5
    assert "My local edit" in body

    # Bekreft at submit nå går igjennom (base matcher remote, body er endret)
    entry = edit.load_edit("1")
    assert entry.modified is True
    assert entry.base_version == 5
