from datetime import datetime, timedelta, timezone

from confluence_sync.blame import Attribution, BlameLine
from confluence_sync.trust import (
    TrustConfig,
    cache_is_fresh,
    compute_trust,
    doc_type_score,
    load_trust_cache,
    recency_score,
    save_trust_cache,
    score_to_dict,
    stability_score,
)


NOW = datetime(2026, 5, 21, tzinfo=timezone.utc)


def _iso(days_ago: int) -> str:
    return (NOW - timedelta(days=days_ago)).isoformat().replace("+00:00", "Z")


def _line(author_id: str, author_name: str, days_ago: int, version: int = 1) -> BlameLine:
    return BlameLine(
        line="x",
        attribution=Attribution(
            version=version,
            author_id=author_id,
            author_name=author_name,
            created_at=_iso(days_ago),
        ),
    )


def test_recency_fresh_is_near_one():
    assert recency_score(_iso(0), NOW, half_life_days=365.0) > 0.99


def test_recency_at_half_life_is_half():
    assert abs(recency_score(_iso(365), NOW, half_life_days=365.0) - 0.5) < 0.01


def test_recency_unknown_date_is_neutral():
    assert recency_score("", NOW, half_life_days=365.0) == 0.5


def test_doc_type_draft_lowered():
    cfg = TrustConfig()
    score, pattern = doc_type_score("Kladd — testing", cfg)
    assert score == 0.4
    assert pattern is not None


def test_doc_type_spec_raised():
    cfg = TrustConfig()
    score, _ = doc_type_score("Arkitektur og krav", cfg)
    assert score == 1.0


def test_doc_type_default_when_no_match():
    cfg = TrustConfig()
    score, pattern = doc_type_score("Tilfeldig sidetittel", cfg)
    assert score == cfg.default_type_weight
    assert pattern is None


def test_stability_more_editors_higher_score():
    few = [{"authorId": "a", "createdAt": _iso(10)}]
    many = [
        {"authorId": f"u{i}", "createdAt": _iso(10 - i)} for i in range(5)
    ]
    score_few, _ = stability_score(few, NOW, 365.0)
    score_many, stats = stability_score(many, NOW, 365.0)
    assert score_many > score_few
    assert stats["unique_editors"] == 5


def test_compute_trust_high_for_fresh_spec_with_many_editors():
    lines = [_line("alice", "Alice", days_ago=5)] * 10
    versions = [
        {"authorId": "alice", "createdAt": _iso(5)},
        {"authorId": "bob", "createdAt": _iso(60)},
        {"authorId": "carol", "createdAt": _iso(120)},
    ]
    score = compute_trust("Arkitektur-policy", lines, versions, now=NOW)

    assert score.level in ("A", "B")
    assert score.components.recency > 0.95
    assert score.components.doc_type == 1.0


def test_compute_trust_low_for_stale_solo_page():
    lines = [_line("ghost", "Old Author", days_ago=1500)] * 100
    versions = [{"authorId": "ghost", "createdAt": _iso(1500)}]
    score = compute_trust("Møtereferat 2021", lines, versions, now=NOW)

    assert score.level in ("D", "F")
    assert score.components.recency < 0.1
    assert any("over 2 år gamle" in f for f in score.flags)


def test_compute_trust_does_not_expose_author_component():
    lines = [_line("x", "Anyone", days_ago=10)]
    versions = [{"authorId": "x", "createdAt": _iso(10)}]
    score = compute_trust("Spec", lines, versions, now=NOW)

    assert not hasattr(score.components, "authors")
    assert "top_authors" not in score.stats


def test_compute_trust_empty_blame_is_zero():
    versions = [{"authorId": "x", "createdAt": _iso(0)}]
    score = compute_trust("Whatever", [], versions, now=NOW)
    assert score.total < 0.5
    assert score.level in ("D", "F")


def test_score_to_dict_roundtrip_is_json_serializable():
    import json

    lines = [_line("a", "Alice", days_ago=10)]
    versions = [{"authorId": "a", "createdAt": _iso(10)}]
    score = compute_trust("Spec", lines, versions, now=NOW)

    payload = score_to_dict(score)
    encoded = json.dumps(payload, ensure_ascii=False)
    decoded = json.loads(encoded)

    assert decoded["level"] == score.level
    assert decoded["components"]["recency"] == round(score.components.recency, 4)
    assert decoded["stats"]["line_count"] == score.stats["line_count"]


def test_trust_cache_roundtrip(tmp_path):
    cache_in = {
        "page-1": {
            "version": 3,
            "total": 0.71,
            "level": "B",
            "components": {"recency": 0.8, "doc_type": 0.7, "stability": 0.5},
            "flags": [],
            "stats": {"line_count": 10},
            "computed_at": "2026-05-20T00:00:00Z",
        }
    }
    save_trust_cache(tmp_path, cache_in)
    loaded = load_trust_cache(tmp_path)
    assert loaded == cache_in


def test_trust_cache_load_missing_returns_empty(tmp_path):
    assert load_trust_cache(tmp_path) == {}


def test_trust_cache_load_corrupt_returns_empty(tmp_path):
    from confluence_sync.trust import CACHE_FILENAME

    (tmp_path / CACHE_FILENAME).write_text("not-json{")
    assert load_trust_cache(tmp_path) == {}


def test_cache_is_fresh_only_when_versions_match():
    assert cache_is_fresh({"version": 5}, 5) is True
    assert cache_is_fresh({"version": 4}, 5) is False
    assert cache_is_fresh(None, 5) is False
    assert cache_is_fresh({}, 5) is False
