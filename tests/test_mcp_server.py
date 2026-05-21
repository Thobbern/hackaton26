import asyncio
from pathlib import Path

import pytest

pytest.importorskip("mcp")

from atlassinate.mcp_server import create_server


def test_create_server_registers_expected_tools(tmp_path: Path):
    server = create_server(tmp_path)

    tools = asyncio.run(server.list_tools())
    tool_names = {t.name for t in tools}

    assert "search_docs" in tool_names
    assert "docs_info" in tool_names


def test_docs_info_reports_missing_index(tmp_path: Path):
    server = create_server(tmp_path)

    result = asyncio.run(server.call_tool("docs_info", {}))

    payload = result[0] if isinstance(result, tuple) else result
    text = str(payload)
    assert "indexed" in text
    assert "false" in text.lower() or "False" in text
