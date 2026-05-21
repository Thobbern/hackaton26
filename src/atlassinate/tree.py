import re
from pathlib import Path


def sanitize_filename(title: str) -> str:
    result = title.strip()
    result = re.sub(r'[/\\:*?"<>|]', '-', result)
    result = result.replace(' ', '-')
    result = result.lower()
    result = result.strip('.')
    result = result[:200]
    return result or "untitled"


def build_page_tree(pages: list[dict]) -> dict:
    page_map = {page["id"]: {"page": page, "children": []} for page in pages}
    roots = []
    for page in pages:
        parent_id = page.get("parentId")
        if parent_id and parent_id in page_map:
            page_map[parent_id]["children"].append(page_map[page["id"]])
        else:
            roots.append(page_map[page["id"]])
    return {"roots": roots}


def build_file_path(page: dict, parent_path: Path, has_children: bool) -> Path:
    name = sanitize_filename(page["title"])
    if has_children:
        return parent_path / name / "index.md"
    return parent_path / f"{name}.md"
