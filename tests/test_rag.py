from atlassinate.rag import chunk_markdown


def test_chunk_empty_returns_empty_list():
    assert chunk_markdown("") == []
    assert chunk_markdown("   \n\n  ") == []


def test_chunk_short_text_returns_single_chunk():
    text = "# Tittel\n\nLitt innhold her."
    chunks = chunk_markdown(text)
    assert len(chunks) == 1
    assert chunks[0].startswith("# Tittel")


def test_chunk_splits_on_headings():
    text = (
        "# En\n\nAvsnitt om en.\n\n"
        "## To\n\nAvsnitt om to.\n\n"
        "### Tre\n\nAvsnitt om tre."
    )
    chunks = chunk_markdown(text, max_chars=50)
    assert len(chunks) >= 3
    assert any(c.startswith("# En") for c in chunks)
    assert any(c.startswith("## To") for c in chunks)
    assert any(c.startswith("### Tre") for c in chunks)


def test_chunk_splits_long_section_by_paragraphs():
    para_a = "A" * 400
    para_b = "B" * 400
    para_c = "C" * 400
    text = f"# Stor seksjon\n\n{para_a}\n\n{para_b}\n\n{para_c}"
    chunks = chunk_markdown(text, max_chars=500, overlap=50)
    assert len(chunks) >= 2
    assert all(len(c) <= 500 for c in chunks)


def test_chunk_hard_splits_very_long_paragraph():
    huge = "X" * 3000
    chunks = chunk_markdown(huge, max_chars=500, overlap=100)
    assert len(chunks) >= 6
    assert all(len(c) <= 500 for c in chunks)
