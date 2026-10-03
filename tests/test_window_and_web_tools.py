"""
Unit tests for Willy's Window Management and Web Research tools.
"""

import sys
import os
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from willy.tools.window_tools import list_open_windows, focus_window, minimize_window
from willy.tools.web_tools import fetch_web_content, search_web
from willy.tools.camera_tools import open_camera_app, take_photo
from willy.tools.registry import TOOL_DEFINITIONS, execute_tool


def test_window_tools_signatures():
    """Verify window tools run and return proper dictionary structures."""
    win_res = list_open_windows()
    assert isinstance(win_res, dict)
    assert "success" in win_res
    assert "windows" in win_res

    # Test focus on non-existent window handles gracefully
    non_existent = focus_window("definitely_not_a_real_app_12345")
    assert non_existent["success"] is False


def test_fetch_web_content():
    """Verify fetch_web_content can extract text from a website."""
    res = fetch_web_content("https://stackandcode.com")
    assert res["success"] is True, f"Web fetch failed: {res}"
    assert "title" in res
    assert "stackandcode" in res["title"].lower()
    assert "description" in res
    assert len(res["description"]) > 0 or len(res["content"]) > 0


def test_search_web():
    """Verify search_web returns structured results."""
    res = search_web("Python programming")
    assert res["success"] is True
    assert len(res["results"]) > 0
    assert "title" in res["results"][0]


def test_new_tools_in_registry():
    """Verify new tools are registered in tool definitions and callable via execute_tool."""
    tool_names = [t["function"]["name"] for t in TOOL_DEFINITIONS]
    expected_new_tools = [
        "focus_window",
        "list_open_windows",
        "minimize_window",
        "fetch_web_content",
        "search_web",
        "take_photo",
        "open_camera_app",
    ]
    for nt in expected_new_tools:
        assert nt in tool_names, f"Tool '{nt}' is missing from TOOL_DEFINITIONS."

    # Test dispatcher for list_open_windows
    dispatch_res = execute_tool("list_open_windows", {})
    assert dispatch_res["success"] is True


if __name__ == "__main__":
    test_window_tools_signatures()
    test_fetch_web_content()
    test_search_web()
    test_new_tools_in_registry()
    print("All window and web tests passed!")
