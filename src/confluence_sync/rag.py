"""RAG-indeksering og søk for synkede Confluence-sider.

Chunker hver markdown-fil på headings, embedder med sentence-transformers
og lagrer i SQLite. Søk laster alle vektorer inn i minne og gjør cosine
similarity (brute force er raskt nok for noen tusen chunks).
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from confluence_sync.frontmatter import read_frontmatter

DEFAULT_MODEL = "intfloat/multilingual-e5-small"
DEFAULT_DB_NAME = ".confluence-sync.rag.db"
DEFAULT_MAX_CHARS = 1200
DEFAULT_OVERLAP = 200


class RagDependencyError(RuntimeError):
    """Reises når valgfrie RAG-avhengigheter mangler."""


def _require_deps():
    try:
        import numpy  # noqa: F401
        from sentence_transformers import SentenceTransformer  # noqa: F401
    except ImportError as e:
        raise RagDependencyError(
            "RAG-funksjonalitet krever ekstra avhengigheter. "
            'Installer med: uv pip install -e ".[rag]"'
        ) from e


@dataclass
class SearchHit:
    confluence_id: str
    file_path: str
    title: str
    chunk_idx: int
    text: str
    score: float


def chunk_markdown(
    text: str,
    max_chars: int = DEFAULT_MAX_CHARS,
    overlap: int = DEFAULT_OVERLAP,
) -> list[str]:
    """Del markdown i chunks ved overskrifter, med fallback til avsnitt."""
    text = text.strip()
    if not text:
        return []

    sections = re.split(r"(?m)^(?=#{1,3}\s)", text)
    sections = [s.strip() for s in sections if s.strip()]
    if not sections:
        sections = [text]

    chunks: list[str] = []
    for section in sections:
        if len(section) <= max_chars:
            chunks.append(section)
            continue

        paragraphs = re.split(r"\n\s*\n", section)
        current = ""
        for para in paragraphs:
            para = para.strip()
            if not para:
                continue
            if len(para) > max_chars:
                if current:
                    chunks.append(current)
                    current = ""
                step = max(1, max_chars - overlap)
                for i in range(0, len(para), step):
                    chunks.append(para[i : i + max_chars])
                continue
            candidate = f"{current}\n\n{para}" if current else para
            if len(candidate) <= max_chars:
                current = candidate
            else:
                if current:
                    chunks.append(current)
                current = para
        if current:
            chunks.append(current)

    return chunks


def _open_db(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS chunks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            confluence_id TEXT NOT NULL,
            file_path TEXT NOT NULL,
            title TEXT NOT NULL,
            chunk_idx INTEGER NOT NULL,
            text TEXT NOT NULL,
            version INTEGER NOT NULL,
            embedding BLOB NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_chunks_cid ON chunks(confluence_id);

        CREATE TABLE IF NOT EXISTS index_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        """
    )


def _get_meta(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM index_meta WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None


def _set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO index_meta (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


def _check_or_set_model(conn: sqlite3.Connection, model_name: str, dim: int) -> None:
    existing_model = _get_meta(conn, "model_name")
    existing_dim = _get_meta(conn, "embedding_dim")

    if existing_model is None:
        _set_meta(conn, "model_name", model_name)
        _set_meta(conn, "embedding_dim", str(dim))
        return

    if existing_model != model_name or existing_dim != str(dim):
        raise RuntimeError(
            f"Indeksen er bygget med modell '{existing_model}' (dim={existing_dim}), "
            f"men prøver nå å bruke '{model_name}' (dim={dim}). "
            f"Slett {DEFAULT_DB_NAME} og bygg indeksen på nytt for å bytte modell."
        )


def build_index(
    docs_dir: Path,
    db_path: Path,
    model_name: str = DEFAULT_MODEL,
    progress_callback: Callable[[str], None] | None = None,
) -> dict:
    """Bygg/oppdater RAG-indeksen for alle .md-filer under docs_dir.

    Inkrementell: sider med uendret version i frontmatter hoppes over.
    Returnerer dict med antall nye, oppdaterte, uendrede og fjernede sider.
    """
    _require_deps()
    import numpy as np
    from sentence_transformers import SentenceTransformer

    model = SentenceTransformer(model_name)
    dim = model.get_sentence_embedding_dimension()

    conn = _open_db(db_path)
    _ensure_schema(conn)
    _check_or_set_model(conn, model_name, dim)

    indexed_versions = {
        row[0]: row[1]
        for row in conn.execute(
            "SELECT confluence_id, MAX(version) FROM chunks GROUP BY confluence_id"
        )
    }

    new_count = 0
    updated_count = 0
    unchanged_count = 0
    seen_ids: set[str] = set()

    for filepath in sorted(docs_dir.rglob("*.md")):
        try:
            meta, body = read_frontmatter(filepath)
        except Exception:
            continue

        seen_ids.add(meta.confluence_id)
        existing_version = indexed_versions.get(meta.confluence_id)

        if existing_version == meta.version:
            unchanged_count += 1
            continue

        chunks = chunk_markdown(body)
        if not chunks:
            continue

        prefixed = [f"passage: {c}" for c in chunks]
        embeddings = model.encode(
            prefixed, normalize_embeddings=True, show_progress_bar=False
        )

        try:
            rel_path = str(filepath.relative_to(docs_dir))
        except ValueError:
            rel_path = str(filepath)

        conn.execute(
            "DELETE FROM chunks WHERE confluence_id = ?", (meta.confluence_id,)
        )
        conn.executemany(
            "INSERT INTO chunks "
            "(confluence_id, file_path, title, chunk_idx, text, version, embedding) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    meta.confluence_id,
                    rel_path,
                    meta.title,
                    i,
                    chunk_text,
                    meta.version,
                    np.asarray(emb, dtype=np.float32).tobytes(),
                )
                for i, (chunk_text, emb) in enumerate(zip(chunks, embeddings))
            ],
        )

        if existing_version is None:
            new_count += 1
        else:
            updated_count += 1

        if progress_callback is not None:
            progress_callback(meta.title)

    removed_ids = set(indexed_versions.keys()) - seen_ids
    for cid in removed_ids:
        conn.execute("DELETE FROM chunks WHERE confluence_id = ?", (cid,))

    conn.commit()
    conn.close()

    return {
        "new": new_count,
        "updated": updated_count,
        "unchanged": unchanged_count,
        "removed": len(removed_ids),
    }


def search(
    query: str,
    db_path: Path,
    top_k: int = 5,
) -> list[SearchHit]:
    """Returnerer top-K chunks for spørringen, sortert etter cosine similarity."""
    _require_deps()
    import numpy as np
    from sentence_transformers import SentenceTransformer

    if not db_path.exists():
        raise FileNotFoundError(
            f"RAG-indeks ikke funnet på {db_path}. Kjør 'gonfluence index' først."
        )

    conn = _open_db(db_path)
    _ensure_schema(conn)

    model_name = _get_meta(conn, "model_name")
    if model_name is None:
        conn.close()
        raise RuntimeError("Indeksen er tom. Kjør 'gonfluence index' først.")

    rows = list(
        conn.execute(
            "SELECT confluence_id, file_path, title, chunk_idx, text, embedding "
            "FROM chunks"
        )
    )
    conn.close()

    if not rows:
        return []

    model = SentenceTransformer(model_name)
    q_emb = model.encode(
        [f"query: {query}"], normalize_embeddings=True, show_progress_bar=False
    )[0].astype(np.float32)

    embs = np.stack([np.frombuffer(r[5], dtype=np.float32) for r in rows])
    scores = embs @ q_emb
    top_idx = np.argsort(-scores)[: max(1, top_k)]

    return [
        SearchHit(
            confluence_id=rows[i][0],
            file_path=rows[i][1],
            title=rows[i][2],
            chunk_idx=int(rows[i][3]),
            text=rows[i][4],
            score=float(scores[i]),
        )
        for i in top_idx
    ]


def index_stats(db_path: Path) -> dict | None:
    """Returner enkel statistikk om indeksen, eller None hvis den ikke finnes."""
    if not db_path.exists():
        return None
    conn = _open_db(db_path)
    _ensure_schema(conn)
    chunks = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    pages = conn.execute(
        "SELECT COUNT(DISTINCT confluence_id) FROM chunks"
    ).fetchone()[0]
    model_name = _get_meta(conn, "model_name")
    dim = _get_meta(conn, "embedding_dim")
    conn.close()
    return {
        "pages": pages,
        "chunks": chunks,
        "model_name": model_name,
        "embedding_dim": dim,
    }
