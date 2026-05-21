"""Tester for enveis-mirror i atlassinate.sync."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from atlassinate.models import SyncState
from atlassinate.sync import mirror_space


def _summary(page_id: str, title: str, version: int, parent_id: str | None = None) -> dict:
    return {
        "id": page_id,
        "title": title,
        "parentId": parent_id,
        "version": {"number": version},
    }


def _full_page(page_id: str, title: str, version: int, body: str, parent_id: str | None = None) -> dict:
    return {
        "id": page_id,
        "title": title,
        "parentId": parent_id,
        "version": {"number": version},
        "body": {"storage": {"value": f"<p>{body}</p>"}},
    }


def _make_client(summaries: list[dict], pages: dict[str, dict]) -> MagicMock:
    client = MagicMock()
    client.base_url = "https://example.atlassian.net/wiki/api/v2"
    client.get_space_page_summaries.return_value = summaries
    client.get_page.side_effect = lambda pid: pages[pid]
    return client


def test_mirror_fresh_pulls_all_pages(tmp_path):
    client = _make_client(
        summaries=[_summary("1", "Alpha", 1)],
        pages={"1": _full_page("1", "Alpha", 1, "hello")},
    )

    result = mirror_space("DEV", tmp_path, client)

    assert result["pulled"] == 1
    assert result["skipped"] == 0
    assert result["removed"] == 0
    assert (tmp_path / "alpha.md").exists()


def test_mirror_writes_new_state_file(tmp_path):
    client = _make_client(
        summaries=[_summary("1", "Alpha", 1)],
        pages={"1": _full_page("1", "Alpha", 1, "hello")},
    )

    mirror_space("DEV", tmp_path, client)

    assert (tmp_path / ".atlassinate-sync.json").exists()
    assert not (tmp_path / ".confluence-sync.json").exists()


def test_mirror_incremental_skips_unchanged(tmp_path):
    client = _make_client(
        summaries=[_summary("1", "Alpha", 5)],
        pages={"1": _full_page("1", "Alpha", 5, "hello")},
    )
    mirror_space("DEV", tmp_path, client)

    client.get_page.reset_mock()

    result = mirror_space("DEV", tmp_path, client)

    assert result["pulled"] == 0
    assert result["skipped"] == 1
    client.get_page.assert_not_called()


def test_mirror_incremental_repulls_changed(tmp_path):
    client = _make_client(
        summaries=[_summary("1", "Alpha", 1)],
        pages={"1": _full_page("1", "Alpha", 1, "v1")},
    )
    mirror_space("DEV", tmp_path, client)

    client.get_space_page_summaries.return_value = [_summary("1", "Alpha", 2)]
    client.get_page.side_effect = lambda pid: _full_page("1", "Alpha", 2, "v2")

    result = mirror_space("DEV", tmp_path, client)

    assert result["pulled"] == 1
    assert result["skipped"] == 0


def test_mirror_removes_deleted_pages(tmp_path):
    client = _make_client(
        summaries=[_summary("1", "Alpha", 1), _summary("2", "Beta", 1)],
        pages={
            "1": _full_page("1", "Alpha", 1, "a"),
            "2": _full_page("2", "Beta", 1, "b"),
        },
    )
    mirror_space("DEV", tmp_path, client)
    assert (tmp_path / "beta.md").exists()

    client.get_space_page_summaries.return_value = [_summary("1", "Alpha", 1)]

    result = mirror_space("DEV", tmp_path, client)

    assert result["removed"] == 1
    assert not (tmp_path / "beta.md").exists()
    state = SyncState.load(tmp_path)
    assert "2" not in state.pages


def test_mirror_full_mode_ignores_state(tmp_path):
    client = _make_client(
        summaries=[_summary("1", "Alpha", 5)],
        pages={"1": _full_page("1", "Alpha", 5, "hello")},
    )
    mirror_space("DEV", tmp_path, client)
    client.get_page.reset_mock()

    result = mirror_space("DEV", tmp_path, client, incremental=False)

    assert result["pulled"] == 1
    assert result["skipped"] == 0
    client.get_page.assert_called()


def test_mirror_reads_legacy_state_file(tmp_path):
    """Eksisterende `.confluence-sync.json` skal leses (én gang), så skrives nytt navn."""
    legacy = tmp_path / ".confluence-sync.json"
    legacy.write_text(
        '{"version": 1, "instance_url": "x", "space_key": "DEV", '
        '"last_full_sync": "2024-01-01T00:00:00", '
        '"pages": {"1": {"confluence_id": "1", "space_key": "DEV", '
        '"title": "Alpha", "version": 1, "parent_id": null, '
        '"last_synced": "2024-01-01T00:00:00", "content_hash": ""}}}'
    )

    client = _make_client(
        summaries=[_summary("1", "Alpha", 1)],
        pages={"1": _full_page("1", "Alpha", 1, "hello")},
    )

    result = mirror_space("DEV", tmp_path, client)

    assert result["skipped"] == 1
    assert (tmp_path / ".atlassinate-sync.json").exists()


def test_mirror_creates_output_dir(tmp_path):
    target = tmp_path / "nested" / "mirror"
    client = _make_client(
        summaries=[_summary("1", "Alpha", 1)],
        pages={"1": _full_page("1", "Alpha", 1, "hello")},
    )

    mirror_space("DEV", target, client)

    assert target.is_dir()
    assert (target / "alpha.md").exists()
