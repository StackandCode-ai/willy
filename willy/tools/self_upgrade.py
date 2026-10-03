"""
Willy Autonomous Self-Upgrade & Self-Evolution Engine.
Allows Willy to inspect, write, test, hot-reload, and upgrade her own capabilities and tools.
"""

import os
import sys
import ast
import time
import shutil
import importlib
import subprocess
import threading
from typing import Dict, Any, Optional

WORKSPACE_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
CUSTOM_TOOLS_DIR = os.path.join(os.path.dirname(__file__), "custom")


def inspect_self(target: str = "summary") -> Dict[str, Any]:
    """
    Allows Willy to inspect her internal architecture, tools, environment, or specific source files.
    Targets:
      - 'summary': High-level overview of Willy's version, python runtime, and modules.
      - 'tools': Lists all currently loaded built-in and custom tools.
      - 'files': Lists all source files in the Willy codebase.
      - Any relative file path (e.g., 'willy/tools/system_tools.py', 'willy/config.py') to read its contents.
    """
    target = target.strip()
    try:
        if target == "summary":
            import platform
            return {
                "success": True,
                "target": "summary",
                "python_version": platform.python_version(),
                "python_executable": sys.executable,
                "os": f"{platform.system()} {platform.release()}",
                "workspace": WORKSPACE_ROOT,
                "custom_tools_dir": CUSTOM_TOOLS_DIR,
            }

        elif target == "tools":
            from willy.tools.registry import get_all_tool_definitions
            tools = get_all_tool_definitions()
            tool_summaries = [
                {
                    "name": t["function"]["name"],
                    "description": t["function"]["description"],
                }
                for t in tools
            ]
            return {
                "success": True,
                "total_tools": len(tool_summaries),
                "tools": tool_summaries,
            }

        elif target == "files":
            file_tree = []
            for root, dirs, files in os.walk(os.path.join(WORKSPACE_ROOT, "willy")):
                if "__pycache__" in root:
                    continue
                for f in files:
                    rel_path = os.path.relpath(os.path.join(root, f), WORKSPACE_ROOT)
                    file_tree.append(rel_path.replace("\\", "/"))
            return {
                "success": True,
                "files": file_tree,
            }

        else:
            # Assume target is a file path
            full_path = os.path.abspath(os.path.join(WORKSPACE_ROOT, target))
            if not full_path.startswith(WORKSPACE_ROOT):
                return {"success": False, "error": "Access denied: Path is outside Willy workspace"}

            if not os.path.isfile(full_path):
                return {"success": False, "error": f"File not found: {target}"}

            with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read(5000)

            # Mask API keys if .env is inspected
            if target.endswith(".env"):
                lines = []
                for line in content.splitlines():
                    if "KEY" in line and "=" in line:
                        k, _ = line.split("=", 1)
                        lines.append(f"{k}=********")
                    else:
                        lines.append(line)
                content = "\n".join(lines)

            return {
                "success": True,
                "file": target,
                "content": content,
            }

    except Exception as e:
        return {"success": False, "error": f"Self-inspection error: {str(e)}"}


def install_python_package(package_name: str) -> Dict[str, Any]:
    """
    Installs a Python package into Willy's active Python environment using pip.
    """
    pkg_clean = package_name.strip()
    if not pkg_clean or any(c in pkg_clean for c in [";", "&", "|", ">", "<"]):
        return {"success": False, "error": f"Invalid package name: {package_name}"}

    start_time = time.time()
    try:
        cmd = [sys.executable, "-m", "pip", "install", pkg_clean]
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=180,
            encoding="utf-8",
            errors="replace",
        )
        duration = round(time.time() - start_time, 2)
        stdout = proc.stdout.strip()
        stderr = proc.stderr.strip()

        if proc.returncode == 0:
            return {
                "success": True,
                "package": pkg_clean,
                "message": f"Successfully installed '{pkg_clean}' in {duration}s.",
                "stdout": stdout[-1000:] if len(stdout) > 1000 else stdout,
            }
        else:
            return {
                "success": False,
                "package": pkg_clean,
                "exit_code": proc.returncode,
                "error": stderr or stdout,
            }
    except Exception as e:
        return {"success": False, "package": pkg_clean, "error": str(e)}


def create_or_update_tool(
    tool_name: str,
    description: str,
    parameters: Dict[str, Any],
    python_code: str,
) -> Dict[str, Any]:
    """
    Creates or updates a custom tool in Willy's custom tools library (willy/tools/custom/).
    Automatically validates the Python syntax with ast.parse() before writing to prevent errors.
    Automatically hot-reloads the tool registry so the new tool is immediately usable.

    The python_code MUST define:
        def run(arguments: dict) -> dict:
            ...
    """
    tool_name = tool_name.strip().lower()
    # Sanitize tool name
    if not tool_name.isidentifier():
        return {
            "success": False,
            "error": f"Invalid tool name '{tool_name}'. Must be a valid Python identifier (letters, numbers, underscores)."
        }

    # Verify python syntax before doing anything
    try:
        parsed_ast = ast.parse(python_code)
    except SyntaxError as e:
        return {
            "success": False,
            "error": f"SyntaxError in generated code at line {e.lineno}: {e.msg}\nCode:\n{e.text}",
        }

    # Verify that a `run` function exists
    has_run_func = any(
        isinstance(node, ast.FunctionDef) and node.name == "run"
        for node in parsed_ast.body
    )
    if not has_run_func:
        return {
            "success": False,
            "error": "The Python code must contain a 'def run(arguments: dict) -> dict:' entrypoint function.",
        }

    os.makedirs(CUSTOM_TOOLS_DIR, exist_ok=True)
    tool_file_path = os.path.join(CUSTOM_TOOLS_DIR, f"{tool_name}.py")

    # Format the complete file
    import json
    param_str = json.dumps(parameters, indent=4)
    file_content = f'''"""
Custom Tool: {tool_name}
Description: {description}
Generated by Willy Self-Upgrade Engine
"""

from typing import Dict, Any

TOOL_DEF = {{
    "type": "function",
    "function": {{
        "name": "{tool_name}",
        "description": """{description}""",
        "parameters": {param_str}
    }}
}}

{python_code}
'''

    # Final ast validation on complete file content
    try:
        ast.parse(file_content)
    except SyntaxError as e:
        return {
            "success": False,
            "error": f"SyntaxError in formatted module: {e.msg} at line {e.lineno}",
        }

    # Backup existing if it exists
    if os.path.exists(tool_file_path):
        try:
            shutil.copyfile(tool_file_path, tool_file_path + ".bak")
        except Exception:
            pass

    try:
        with open(tool_file_path, "w", encoding="utf-8") as f:
            f.write(file_content)

        # Trigger hot-reload of tools
        reload_result = reload_willy_tools()

        return {
            "success": True,
            "tool_name": tool_name,
            "file_path": f"willy/tools/custom/{tool_name}.py",
            "message": f"Successfully created tool '{tool_name}' and hot-reloaded into active memory!",
            "reload_status": reload_result,
        }
    except Exception as e:
        return {"success": False, "error": f"Failed to save tool '{tool_name}': {str(e)}"}


def edit_willy_source(file_path: str, new_content: str, create_backup: bool = True) -> Dict[str, Any]:
    """
    Safely modifies any file in Willy's codebase with syntax verification and backup.
    """
    clean_path = os.path.abspath(os.path.join(WORKSPACE_ROOT, file_path))
    if not clean_path.startswith(WORKSPACE_ROOT):
        return {"success": False, "error": "Access denied: Path is outside Willy workspace"}

    # If python file, validate syntax before writing
    if clean_path.endswith(".py"):
        try:
            ast.parse(new_content)
        except SyntaxError as e:
            return {
                "success": False,
                "error": f"SyntaxError at line {e.lineno}: {e.msg}\n{e.text}",
            }

    try:
        if create_backup and os.path.exists(clean_path):
            shutil.copyfile(clean_path, clean_path + ".bak")

        os.makedirs(os.path.dirname(clean_path), exist_ok=True)
        with open(clean_path, "w", encoding="utf-8") as f:
            f.write(new_content)

        return {
            "success": True,
            "file": file_path,
            "message": f"Successfully updated {file_path}",
        }
    except Exception as e:
        return {"success": False, "error": f"Failed to write file {file_path}: {str(e)}"}


def reload_willy_tools() -> Dict[str, Any]:
    """
    Hot-reloads all tools dynamically from disk into active memory.
    """
    try:
        from willy.tools.registry import reload_tools
        loaded_count = reload_tools()
        return {
            "success": True,
            "message": f"Hot-reloaded {loaded_count} tools into active memory.",
            "total_tools": loaded_count,
        }
    except Exception as e:
        return {"success": False, "error": f"Hot-reload error: {str(e)}"}


def restart_willy(delay_sec: float = 1.5) -> Dict[str, Any]:
    """
    Spawns a clean new instance of Willy and terminates the current process.
    Allows time for TTS to vocalize the confirmation before exiting.
    """
    def _do_restart():
        time.sleep(delay_sec)
        python_exe = sys.executable
        args = [python_exe] + sys.argv
        # Spawn detached process
        subprocess.Popen(
            args,
            cwd=WORKSPACE_ROOT,
            creationflags=subprocess.CREATE_NEW_CONSOLE if sys.platform == "win32" else 0,
        )
        os._exit(0)

    t = threading.Thread(target=_do_restart, daemon=True)
    t.start()

    return {
        "success": True,
        "message": f"Willy restart scheduled in {delay_sec} seconds.",
    }
