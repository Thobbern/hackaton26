from confluence_sync.blame import (
    Attribution,
    BlameLine,
    _attribute_lines,
    author_summary,
    filter_since,
)


def _attr(v: int, name: str, created_at: str = "2025-01-01T00:00:00Z") -> Attribution:
    return Attribution(
        version=v, author_id=f"u{v}", author_name=name, created_at=created_at
    )


def test_single_version_attributes_all_lines_to_author():
    lines = ["alpha", "beta", "gamma"]
    attr = _attr(1, "Alice")

    result = _attribute_lines(lines, current=[], version_attr=attr)

    assert [bl.line for bl in result] == lines
    assert all(bl.attribution == attr for bl in result)


def test_insertion_attributes_only_new_lines():
    v1 = _attr(1, "Alice")
    v2 = _attr(2, "Bob")
    current = [BlameLine("alpha", v1), BlameLine("gamma", v1)]

    result = _attribute_lines(["alpha", "beta", "gamma"], current, v2)

    assert [bl.line for bl in result] == ["alpha", "beta", "gamma"]
    assert result[0].attribution == v1  # unchanged
    assert result[1].attribution == v2  # newly inserted
    assert result[2].attribution == v1  # unchanged


def test_replacement_attributes_only_replaced_lines():
    v1 = _attr(1, "Alice")
    v2 = _attr(2, "Bob")
    current = [BlameLine("alpha", v1), BlameLine("beta", v1), BlameLine("gamma", v1)]

    result = _attribute_lines(["alpha", "BETA", "gamma"], current, v2)

    assert result[0].attribution == v1
    assert result[1].attribution == v2
    assert result[1].line == "BETA"
    assert result[2].attribution == v1


def test_deletion_does_not_create_lines():
    v1 = _attr(1, "Alice")
    v2 = _attr(2, "Bob")
    current = [BlameLine("alpha", v1), BlameLine("beta", v1), BlameLine("gamma", v1)]

    result = _attribute_lines(["alpha", "gamma"], current, v2)

    assert [bl.line for bl in result] == ["alpha", "gamma"]
    assert all(bl.attribution == v1 for bl in result)


def test_multiple_versions_preserve_oldest_attribution_for_stable_lines():
    v1 = _attr(1, "Alice", "2024-01-01T00:00:00Z")
    v2 = _attr(2, "Bob", "2024-06-01T00:00:00Z")
    v3 = _attr(3, "Carol", "2025-01-01T00:00:00Z")

    state = _attribute_lines(["a", "b", "c"], [], v1)
    state = _attribute_lines(["a", "b", "c", "d"], state, v2)
    state = _attribute_lines(["a", "B-NEW", "c", "d", "e"], state, v3)

    by_line = {bl.line: bl.attribution for bl in state}
    assert by_line["a"] == v1
    assert by_line["B-NEW"] == v3
    assert by_line["c"] == v1
    assert by_line["d"] == v2
    assert by_line["e"] == v3


def test_author_summary_sorts_by_line_count_desc():
    v1 = _attr(1, "Alice", "2024-01-01T00:00:00Z")
    v2 = _attr(2, "Bob", "2024-06-01T00:00:00Z")
    blame = [
        BlameLine("a", v1),
        BlameLine("b", v1),
        BlameLine("c", v1),
        BlameLine("d", v2),
    ]

    summary = author_summary(blame)

    assert summary[0]["author_name"] == "Alice"
    assert summary[0]["lines"] == 3
    assert summary[0]["latest_at"] == "2024-01-01T00:00:00Z"
    assert summary[1]["author_name"] == "Bob"
    assert summary[1]["lines"] == 1


def test_filter_since_keeps_only_lines_on_or_after_date():
    old = _attr(1, "Alice", "2024-01-01T00:00:00Z")
    new = _attr(2, "Bob", "2025-06-01T00:00:00Z")
    blame = [BlameLine("a", old), BlameLine("b", new), BlameLine("c", new)]

    filtered = filter_since(blame, "2025-01-01")

    assert [bl.line for bl in filtered] == ["b", "c"]
