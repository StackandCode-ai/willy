"""
Unit and Integration Tests for Willy's Self-Upgrade & Self-Evolution Engine.
"""

import os
import sys
import shutil
import unittest

# Add project root to sys.path
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from willy.tools.self_upgrade import (
    inspect_self,
    create_or_update_tool,
    reload_willy_tools,
    edit_willy_source,
    CUSTOM_TOOLS_DIR,
)
from willy.tools.registry import (
    get_all_tool_definitions,
    execute_tool,
    reload_tools,
)


class TestSelfUpgrade(unittest.TestCase):

    def setUp(self):
        self.test_tool_name = "test_custom_clock"
        self.test_tool_file = os.path.join(CUSTOM_TOOLS_DIR, f"{self.test_tool_name}.py")

    def tearDown(self):
        # Cleanup test tool file if created
        if os.path.exists(self.test_tool_file):
            try:
                os.remove(self.test_tool_file)
            except Exception:
                pass
        bak_file = self.test_tool_file + ".bak"
        if os.path.exists(bak_file):
            try:
                os.remove(bak_file)
            except Exception:
                pass
        reload_tools()

    def test_inspect_self_summary(self):
        res = inspect_self(target="summary")
        self.assertTrue(res.get("success"))
        self.assertIn("python_version", res)
        self.assertIn("workspace", res)

    def test_inspect_self_tools(self):
        res = inspect_self(target="tools")
        self.assertTrue(res.get("success"))
        self.assertGreaterEqual(res.get("total_tools", 0), 10)
        tool_names = [t["name"] for t in res.get("tools", [])]
        self.assertIn("execute_powershell", tool_names)
        self.assertIn("create_or_update_tool", tool_names)
        self.assertIn("inspect_self", tool_names)
        self.assertIn("reload_willy_tools", tool_names)

    def test_inspect_self_files(self):
        res = inspect_self(target="files")
        self.assertTrue(res.get("success"))
        files = res.get("files", [])
        self.assertTrue(any("willy/tools/self_upgrade.py" in f for f in files))

    def test_inspect_self_read_file(self):
        res = inspect_self(target="willy/config.py")
        self.assertTrue(res.get("success"))
        self.assertIn("class Settings", res.get("content", ""))

    def test_syntax_error_rejection(self):
        """Verify that broken Python code is rejected before writing to disk."""
        broken_code = """
def run(arguments: dict) -> dict:
    broken = [1, 2, 3
    return {"success": True}
"""
        res = create_or_update_tool(
            tool_name=self.test_tool_name,
            description="Test broken tool",
            parameters={"type": "object", "properties": {}},
            python_code=broken_code,
        )
        self.assertFalse(res.get("success"))
        self.assertIn("SyntaxError", res.get("error", ""))
        # Verify file was never written
        self.assertFalse(os.path.exists(self.test_tool_file))

    def test_missing_run_entrypoint_rejection(self):
        """Verify that code without a `run` function is rejected."""
        no_run_code = """
def calculate_something(x: int) -> int:
    return x * 2
"""
        res = create_or_update_tool(
            tool_name=self.test_tool_name,
            description="Tool without run function",
            parameters={"type": "object", "properties": {}},
            python_code=no_run_code,
        )
        self.assertFalse(res.get("success"))
        self.assertIn("entrypoint function", res.get("error", ""))
        self.assertFalse(os.path.exists(self.test_tool_file))

    def test_create_and_execute_custom_tool(self):
        """Create a valid custom tool, hot-reload, and execute it."""
        valid_code = """
import time

def run(arguments: dict) -> dict:
    timezone = arguments.get("timezone", "UTC")
    return {
        "success": True,
        "current_epoch": time.time(),
        "timezone": timezone,
        "message": f"Clock checked successfully for {timezone}"
    }
"""
        res = create_or_update_tool(
            tool_name=self.test_tool_name,
            description="Returns current system epoch and timezone info.",
            parameters={
                "type": "object",
                "properties": {
                    "timezone": {"type": "string", "description": "Target timezone"}
                },
            },
            python_code=valid_code,
        )
        self.assertTrue(res.get("success"), f"Creation failed: {res.get('error')}")
        self.assertTrue(os.path.exists(self.test_tool_file))

        # Check that tool was loaded into tool definitions
        tools = get_all_tool_definitions()
        matching_defs = [t for t in tools if t["function"]["name"] == self.test_tool_name]
        self.assertEqual(len(matching_defs), 1)

        # Execute the newly created tool
        exec_res = execute_tool(self.test_tool_name, {"timezone": "EST"})
        self.assertTrue(exec_res.get("success"))
        self.assertEqual(exec_res.get("timezone"), "EST")
        self.assertIn("current_epoch", exec_res)


if __name__ == "__main__":
    unittest.main()
