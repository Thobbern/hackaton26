"""MCP-server som eksponerer RAG-søk som verktøy for Claude Code.

Når denne kjører, kan Claude (i en hvilken som helst `claude`-sesjon hvor
serveren er registrert) kalle `search_docs` selv som et verktøy — på samme
måte som Grep og Read. Dette gir interaktiv RAG: modellen bestemmer når
og hvor mange ganger den vil søke.

Server kommuniserer over stdio. Aldri skriv til stdout under run() —
det ville korrupt MCP-protokollen.
"""

from __future__ import annotations

from pathlib import Path

from atlassinate.rag import DEFAULT_DB_NAME, index_stats, search


def create_server(docs_dir: Path):
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP("gonfluence")
    db_path = docs_dir / DEFAULT_DB_NAME

    @mcp.tool()
    def search_docs(query: str, top_k: int = 5) -> list[dict]:
        """Semantisk søk over synkede Confluence-sider.

        Returnerer de top-K mest relevante utdragene (chunks) basert på
        cosine similarity mot spørringen. Hvert treff inneholder absolutt
        filsti (kan brukes direkte med Read), tittel, relevansscore (0-1)
        og selve tekstutdraget. Bruk dette når du leter etter dokumentasjon
        basert på mening framfor eksakte nøkkelord.

        Args:
            query: Spørringen (på norsk eller engelsk).
            top_k: Antall utdrag å returnere (default 5, maks anbefalt ~20).
        """
        hits = search(query, db_path, top_k=top_k)
        return [
            {
                "file_path": str((docs_dir / h.file_path).resolve()),
                "relative_path": h.file_path,
                "title": h.title,
                "chunk_idx": h.chunk_idx,
                "score": round(h.score, 4),
                "text": h.text,
            }
            for h in hits
        ]

    @mcp.tool()
    def docs_info() -> dict:
        """Returnerer metadata om RAG-indeksen.

        Brukbart for å sjekke om indeksen finnes, hvor mange sider/chunks
        som er indeksert, og hvilken embedding-modell som ble brukt.
        """
        stats = index_stats(db_path)
        if stats is None:
            return {
                "indexed": False,
                "docs_dir": str(docs_dir),
                "hint": "Kjør 'gonfluence index' i docs-mappa først.",
            }
        return {"indexed": True, "docs_dir": str(docs_dir), **stats}

    return mcp


def run_stdio(docs_dir: Path) -> None:
    """Start MCP-serveren på stdio. Blokker til klienten kobler fra."""
    server = create_server(docs_dir)
    server.run()
