from pathlib import Path
import frontmatter

from confluence_sync.models import PageMeta


def read_frontmatter(filepath: Path) -> tuple[PageMeta, str]:
    post = frontmatter.load(str(filepath))
    meta = PageMeta.from_dict(dict(post.metadata))
    return meta, post.content


def write_frontmatter(filepath: Path, meta: PageMeta, body: str) -> None:
    filepath.parent.mkdir(parents=True, exist_ok=True)
    post = frontmatter.Post(body, **meta.to_dict())
    filepath.write_text(frontmatter.dumps(post))
