from pathlib import Path

from atlassinate.tree import build_file_path, build_page_tree, sanitize_filename


def test_sanitize_filename_normal():
    assert sanitize_filename("My Page Title") == "my-page-title"


def test_sanitize_filename_special_chars():
    assert sanitize_filename('File: "test" <v2>') == "file---test---v2-"


def test_sanitize_filename_empty():
    assert sanitize_filename("...") == "untitled"


def test_sanitize_filename_long():
    long_title = "a" * 250
    result = sanitize_filename(long_title)
    assert len(result) <= 200


def test_build_page_tree_flat():
    pages = [
        {"id": "1", "title": "Alpha"},
        {"id": "2", "title": "Beta"},
        {"id": "3", "title": "Gamma"},
    ]
    tree = build_page_tree(pages)
    assert len(tree["roots"]) == 3
    root_ids = {node["page"]["id"] for node in tree["roots"]}
    assert root_ids == {"1", "2", "3"}


def test_build_page_tree_nested():
    pages = [
        {"id": "1", "title": "Parent"},
        {"id": "2", "title": "Child", "parentId": "1"},
        {"id": "3", "title": "Grandchild", "parentId": "2"},
    ]
    tree = build_page_tree(pages)

    assert len(tree["roots"]) == 1
    parent_node = tree["roots"][0]
    assert parent_node["page"]["id"] == "1"
    assert len(parent_node["children"]) == 1

    child_node = parent_node["children"][0]
    assert child_node["page"]["id"] == "2"
    assert len(child_node["children"]) == 1

    grandchild_node = child_node["children"][0]
    assert grandchild_node["page"]["id"] == "3"
    assert grandchild_node["children"] == []


def test_build_file_path_leaf():
    page = {"id": "1", "title": "My Page"}
    path = build_file_path(page, Path("/docs"), has_children=False)
    assert path == Path("/docs/my-page.md")


def test_build_file_path_with_children():
    page = {"id": "1", "title": "My Page"}
    path = build_file_path(page, Path("/docs"), has_children=True)
    assert path == Path("/docs/my-page/index.md")
