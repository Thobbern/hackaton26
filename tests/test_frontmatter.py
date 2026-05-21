from pathlib import Path

import pytest

from atlassinate.frontmatter import read_frontmatter, write_frontmatter
from atlassinate.models import PageMeta, SyncState


def make_meta(**kwargs) -> PageMeta:
    defaults = dict(
        confluence_id="123",
        space_key="TEAM",
        title="My Page",
        version=5,
        parent_id="99",
        last_synced="2024-01-01T00:00:00",
        content_hash="abc123",
    )
    defaults.update(kwargs)
    return PageMeta(**defaults)


def test_write_and_read_roundtrip(tmp_path):
    filepath = tmp_path / "page.md"
    meta = make_meta()
    body = "# Hello\n\nThis is the body."

    write_frontmatter(filepath, meta, body)
    read_meta, read_body = read_frontmatter(filepath)

    assert read_meta.confluence_id == meta.confluence_id
    assert read_meta.space_key == meta.space_key
    assert read_meta.title == meta.title
    assert read_meta.version == meta.version
    assert read_meta.parent_id == meta.parent_id
    assert read_meta.last_synced == meta.last_synced
    assert read_meta.content_hash == meta.content_hash
    assert read_body.strip() == body.strip()


def test_frontmatter_creates_parent_dirs(tmp_path):
    filepath = tmp_path / "a" / "b" / "c" / "page.md"
    meta = make_meta()

    write_frontmatter(filepath, meta, "content")

    assert filepath.exists()
    read_meta, _ = read_frontmatter(filepath)
    assert read_meta.confluence_id == meta.confluence_id


def test_page_meta_to_dict_from_dict():
    meta = make_meta()
    d = meta.to_dict()
    restored = PageMeta.from_dict(d)

    assert restored.confluence_id == meta.confluence_id
    assert restored.space_key == meta.space_key
    assert restored.title == meta.title
    assert restored.version == meta.version
    assert restored.parent_id == meta.parent_id
    assert restored.last_synced == meta.last_synced
    assert restored.content_hash == meta.content_hash


def test_sync_state_save_load(tmp_path):
    state = SyncState(
        version=1,
        instance_url="https://example.atlassian.net",
        space_key="PROJ",
        last_full_sync="2024-06-01T12:00:00",
        pages={"123": {"file": "page.md"}},
    )
    state.save(tmp_path)

    loaded = SyncState.load(tmp_path)

    assert loaded.version == state.version
    assert loaded.instance_url == state.instance_url
    assert loaded.space_key == state.space_key
    assert loaded.last_full_sync == state.last_full_sync
    assert loaded.pages == state.pages


def test_sync_state_load_missing_file(tmp_path):
    state = SyncState.load(tmp_path)

    assert state.version == 1
    assert state.instance_url == ""
    assert state.space_key == ""
    assert state.pages == {}


