"""Tests for atlassinate.converter."""

import pathlib
import pytest

from atlassinate.converter import storage_to_markdown, markdown_to_storage

FIXTURES = pathlib.Path(__file__).parent / "fixtures"

# ---------------------------------------------------------------------------
# storage_to_markdown
# ---------------------------------------------------------------------------


def test_code_macro_to_markdown():
    """Confluence code macro is converted to a fenced code block with language."""
    xhtml = (
        '<ac:structured-macro ac:name="code">'
        '  <ac:parameter ac:name="language">python</ac:parameter>'
        '  <ac:plain-text-body><![CDATA[def hello():\n    print("world")]]></ac:plain-text-body>'
        "</ac:structured-macro>"
    )
    md = storage_to_markdown(xhtml)
    assert md.startswith("```python")
    assert "def hello():" in md
    assert md.rstrip().endswith("```")


def test_info_macro_to_markdown():
    """Info macro is converted to a blockquote with bold 'Info:' prefix."""
    xhtml = (
        '<ac:structured-macro ac:name="info">'
        "  <ac:rich-text-body><p>Read the docs first.</p></ac:rich-text-body>"
        "</ac:structured-macro>"
    )
    md = storage_to_markdown(xhtml)
    assert md.startswith(">")
    assert "**Info:**" in md
    assert "Read the docs first." in md


def test_warning_macro_to_markdown():
    """Warning macro is converted to a blockquote with bold 'Warning:' prefix."""
    xhtml = (
        '<ac:structured-macro ac:name="warning">'
        "  <ac:rich-text-body><p>Do not delete production data.</p></ac:rich-text-body>"
        "</ac:structured-macro>"
    )
    md = storage_to_markdown(xhtml)
    assert md.startswith(">")
    assert "**Warning:**" in md
    assert "Do not delete production data." in md


def test_confluence_link_to_markdown():
    """ac:link with ri:page is converted to a Markdown link."""
    xhtml = (
        "<ac:link>"
        '  <ri:page ri:content-title="My Page"/>'
        "  <ac:plain-text-link-body>My Page</ac:plain-text-link-body>"
        "</ac:link>"
    )
    md = storage_to_markdown(xhtml)
    assert "[My Page]" in md
    assert "My%20Page" in md


def test_unknown_macro_preserved():
    """An unknown macro is preserved as an HTML comment placeholder."""
    xhtml = (
        '<ac:structured-macro ac:name="jira">'
        '  <ac:parameter ac:name="key">PROJ-1</ac:parameter>'
        "</ac:structured-macro>"
    )
    md = storage_to_markdown(xhtml)
    assert '<!-- confluence:macro name="jira"' in md
    assert "<!-- /confluence:macro -->" in md


def test_basic_html_to_markdown():
    """Plain HTML with headings, paragraphs, and lists converts correctly."""
    xhtml = "<h1>Title</h1><p>Hello world.</p><ul><li>Item A</li><li>Item B</li></ul>"
    md = storage_to_markdown(xhtml)
    assert md.startswith("# Title")
    assert "Hello world." in md
    assert "- Item A" in md
    assert "- Item B" in md


# ---------------------------------------------------------------------------
# markdown_to_storage
# ---------------------------------------------------------------------------


def test_markdown_to_storage_code():
    """Fenced code block is converted to a Confluence code macro."""
    md = '```python\nprint("hi")\n```'
    html = markdown_to_storage(md)
    assert 'ac:name="code"' in html
    assert 'ac:name="language">python' in html
    assert "<ac:plain-text-body>" in html


def test_markdown_to_storage_admonition():
    """Blockquote with bold 'Info:' prefix is converted to an info macro."""
    md = "> **Info:** Read the docs first."
    html = markdown_to_storage(md)
    assert 'ac:name="info"' in html
    assert "<ac:rich-text-body>" in html
    assert "Read the docs first." in html


# ---------------------------------------------------------------------------
# Roundtrips
# ---------------------------------------------------------------------------


def test_roundtrip_code():
    """Code macro survives storage -> markdown -> storage with language and code intact."""
    original = (
        '<ac:structured-macro ac:name="code">'
        '<ac:parameter ac:name="language">python</ac:parameter>'
        "<ac:plain-text-body><![CDATA[def hello():\n    print(\"world\")]]></ac:plain-text-body>"
        "</ac:structured-macro>"
    )
    md = storage_to_markdown(original)
    # Language must survive the roundtrip
    assert "```python" in md

    back = markdown_to_storage(md)
    assert 'ac:name="code"' in back
    assert 'ac:name="language">python' in back
    # Core code content is preserved (HTML-escaped or not)
    assert "def hello():" in back


def test_roundtrip_basic_html():
    """Simple heading + paragraph text survives a full roundtrip."""
    original = "<h1>Title</h1><p>Hello world.</p>"
    md = storage_to_markdown(original)
    assert "Title" in md
    assert "Hello world." in md

    back = markdown_to_storage(md)
    # Text content must be present in the final storage output
    assert "Title" in back
    assert "Hello world." in back


# ---------------------------------------------------------------------------
# Fixture file smoke-test
# ---------------------------------------------------------------------------


def test_sample_storage_fixture():
    """The sample_storage.html fixture can be converted without raising errors."""
    xhtml = (FIXTURES / "sample_storage.html").read_text()
    md = storage_to_markdown(xhtml)
    assert "Getting Started with Python" in md
    assert "```python" in md
    assert "**Info:**" in md
    assert "**Warning:**" in md
    assert "[Advanced Python]" in md
