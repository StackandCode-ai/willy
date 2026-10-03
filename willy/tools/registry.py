"""
Tool registry and deterministic function schemas for Groq/OpenAI tool calling.
Supports dynamic custom tools, hot-reloading, and self-upgrading.
Optimized for low token overhead.
"""

import os
import sys
import glob
import json
import importlib.util
from typing import Dict, Any, List, Callable

from willy.tools.system_tools import (
    execute_powershell,
    launch_application,
    open_url,
    interact_ui,
    scroll_screen,
    get_system_status,
    volume_control,
    file_operations,
)

# Built-in Tool Definitions matching OpenAI/Groq Tool Calling API
BUILTIN_TOOL_DEFINITIONS: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "execute_powershell",
            "description": "Execute Windows PowerShell commands safely.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "PowerShell command to run."},
                    "timeout": {"type": "integer", "description": "Timeout in seconds."},
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "launch_application",
            "description": "Launch a Windows application or document (e.g. notepad, chrome, calc, spotify).",
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {"type": "string", "description": "Application name or path."},
                    "args": {"type": "string", "description": "Command line arguments."},
                },
                "required": ["target"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "open_url",
            "description": "Open a website URL in default browser.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "URL to open."},
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "interact_ui",
            "description": "Perform keyboard hotkeys, typing, or mouse clicks.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["hotkey", "type", "press", "click", "scroll"]},
                    "keys": {"type": "array", "items": {"type": "string"}},
                    "text": {"type": "string"},
                    "coordinates": {"type": "array", "items": {"type": "integer"}},
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "scroll_screen",
            "description": "Scroll the active window, browser webpage, or document. Supports direction 'down', 'up', 'page_down', 'page_up', 'top', 'bottom'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "direction": {
                        "type": "string",
                        "enum": ["down", "up", "page_down", "page_up", "top", "bottom"],
                        "description": "Direction to scroll.",
                    },
                    "amount": {
                        "type": "integer",
                        "description": "Scroll clicks/speed (default 5 for normal, 10 for fast).",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_workspace_focus_info",
            "description": "Get real-time multi-monitor display topology, exact cursor (x, y) position, which screen has the cursor, and which screen the user is actively working on.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "describe_screen",
            "description": "Look at the user's computer screen and describe what is visible or answer questions about on-screen apps, web pages, error messages, or documents. Supports specific monitors or active monitor.",
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {
                        "type": "string",
                        "description": "Specific question about what is on screen (e.g. 'What website is open?', 'Summarize this article').",
                    },
                    "monitor": {
                        "type": "string",
                        "description": "Which monitor to inspect: 'active' (where cursor is), 'all' (all monitors combined), or monitor index like '1', '2'. Default is 'active'.",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "click_screen_element",
            "description": "Visually locate a button, link, icon, or UI element on screen and click it with the mouse cursor across multiple monitors.",
            "parameters": {
                "type": "object",
                "properties": {
                    "target_description": {
                        "type": "string",
                        "description": "Description of element to click (e.g. 'blue submit button', 'login button', 'shopping cart icon', 'first link').",
                    },
                    "monitor": {
                        "type": "string",
                        "description": "Target monitor: 'active' (default), '1', '2', etc.",
                    },
                },
                "required": ["target_description"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_files",
            "description": "Search the computer for files by keyword, filename, or extension (e.g. 'invoice', 'resume', 'pdf', 'notes'). Optionally opens the file or reveals it in Windows File Explorer.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Filename, keyword, or pattern to search for."},
                    "directory": {"type": "string", "description": "Optional directory to search in (e.g. 'Downloads', 'Documents', 'Desktop', 'D:\\Project'). Default searches all common user folders."},
                    "extension": {"type": "string", "description": "Optional file extension filter (e.g. 'pdf', 'docx', 'xlsx', 'py', 'png')."},
                    "open_file": {"type": "boolean", "description": "If true, automatically opens the first matching file in its default application."},
                    "show_in_explorer": {"type": "boolean", "description": "If true, opens File Explorer and highlights the file."},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "open_file_path",
            "description": "Open any local file or folder path directly in its default Windows app or File Explorer.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Full file path or folder path to open."},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_system_status",
            "description": "Get real-time CPU, RAM, disk, battery, and active window status.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "volume_control",
            "description": "Adjust Windows system volume.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["mute", "unmute", "up", "down", "set"]},
                    "level": {"type": "integer", "description": "0-100 if action is set."},
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "file_operations",
            "description": "Read, write, check, or list local files.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["read", "write", "list", "exists"]},
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["action", "path"],
            },
        },
    },
    # --- Self-Upgrade & Self-Evolution Tools ---
    {
        "type": "function",
        "function": {
            "name": "inspect_self",
            "description": "Inspect Willy's tools, files, or Python runtime.",
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {"type": "string", "description": "'summary', 'tools', 'files', or relative file path."},
                },
                "required": ["target"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "install_python_package",
            "description": "Install a Python package via pip for new tools.",
            "parameters": {
                "type": "object",
                "properties": {
                    "package_name": {"type": "string", "description": "Name of package to install."},
                },
                "required": ["package_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_or_update_tool",
            "description": "Create or update a custom tool in willy/tools/custom/. Validates Python code and hot-reloads it immediately. Code must define 'def run(arguments: dict) -> dict:'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "tool_name": {"type": "string", "description": "Valid Python identifier (e.g. get_weather)."},
                    "description": {"type": "string", "description": "What the tool does."},
                    "parameters": {"type": "object", "description": "JSON schema for tool parameters."},
                    "python_code": {"type": "string", "description": "Python code implementing def run(arguments: dict) -> dict:."},
                },
                "required": ["tool_name", "description", "parameters", "python_code"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_willy_source",
            "description": "Safely edit a Willy source file with syntax validation and backup.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {"type": "string", "description": "Relative file path."},
                    "new_content": {"type": "string", "description": "Full new file content."},
                },
                "required": ["file_path", "new_content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "reload_willy_tools",
            "description": "Hot-reload all custom and built-in tools into memory.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "restart_willy",
            "description": "Cleanly restart Willy daemon or UI.",
            "parameters": {
                "type": "object",
                "properties": {
                    "delay_sec": {"type": "number", "description": "Delay before restarting (default 1.5)."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "launch_browser_with_profile",
            "description": "Launch Microsoft Edge or Google Chrome with a specific user profile (e.g. Work, Personal, or by email) and optional URL.",
            "parameters": {
                "type": "object",
                "properties": {
                    "browser": {"type": "string", "enum": ["edge", "chrome"], "description": "Target browser."},
                    "profile_query": {"type": "string", "description": "Profile name, user email, or number (e.g. 'work', 'personal')."},
                    "url": {"type": "string", "description": "Optional web address to open."},
                },
                "required": ["browser"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "manage_windows_settings",
            "description": "Control Windows system settings. Open settings pages (sound, display, wifi, bluetooth, update, etc.) or toggle dark/light theme.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["open", "theme", "list_pages"], "description": "Settings action."},
                    "page": {"type": "string", "description": "Settings page name for 'open' (e.g. 'sound', 'wifi', 'display', 'bluetooth')."},
                    "value": {"type": "string", "description": "Value for action: 'dark' or 'light' for theme."},
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_app_running",
            "description": "Check if an application or game is currently open/running on Windows.",
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {"type": "string", "description": "Application name (e.g. 'chrome', 'genshin', 'edge', 'notepad')."},
                },
                "required": ["target"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "close_app",
            "description": "Close or terminate a running application.",
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {"type": "string", "description": "Application name to close."},
                    "force": {"type": "boolean", "description": "Force kill if true."},
                },
                "required": ["target"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "lock_workstation",
            "description": "Lock the Windows desktop screen immediately.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "unlock_or_wake",
            "description": "Wake the monitor display and dismiss the lock screen.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pin": {"type": "string", "description": "Optional Windows PIN to enter."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "shutdown_system",
            "description": "Schedule a Windows PC shutdown (default 30s delay).",
            "parameters": {
                "type": "object",
                "properties": {
                    "delay_sec": {"type": "integer", "description": "Delay in seconds before shutdown."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "restart_system",
            "description": "Schedule a Windows PC restart (default 30s delay).",
            "parameters": {
                "type": "object",
                "properties": {
                    "delay_sec": {"type": "integer", "description": "Delay in seconds before restart."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cancel_shutdown",
            "description": "Cancel a pending scheduled shutdown or restart.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_network_status",
            "description": "Check internet connectivity and network latency.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "manage_autostart",
            "description": "Configure or check Willy auto-start with Windows on PC boot/logon. Actions: 'enable' (start automatically on boot), 'disable' (do not start on boot), 'status' (check if enabled).",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["enable", "disable", "status"],
                        "description": "Action to perform ('enable', 'disable', 'status').",
                    },
                    "mode": {
                        "type": "string",
                        "enum": ["ui", "voice"],
                        "description": "Startup mode: 'ui' for floating visualizer widget (recommended), 'voice' for background console.",
                    },
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "focus_window",
            "description": "Bring an application window to the active foreground, unminimizing it if it is minimized or behind other windows. Can target by app name (e.g. 'chrome', 'edge', 'code', 'camera', 'notepad', 'spotify') or window title (e.g. 'StackandCode', 'YouTube').",
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {"type": "string", "description": "Application name or window title to bring to front."},
                },
                "required": ["target"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_open_windows",
            "description": "List all currently open application windows on Windows with their titles and process names.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "minimize_window",
            "description": "Minimize an open window or the active window.",
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {"type": "string", "description": "Application or window title to minimize. If omitted, minimizes active window."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_web_content",
            "description": "Directly fetch and read the text content of any website or URL. Returns page title, meta description, and clean readable text. Use this whenever the user asks about a website, domain, company, or web article.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "URL or domain of the website to read (e.g. 'https://stackandcode.com')."},
                    "max_chars": {"type": "integer", "description": "Maximum characters of content to return (default 2500)."},
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_web",
            "description": "Search the web for information, companies, articles, or queries. Returns search results with titles, snippets, and URLs.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query or topic."},
                    "num_results": {"type": "integer", "description": "Number of results to return (default 5)."},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "take_photo",
            "description": "Take a photo/picture using the Windows Camera app or webcam.",
            "parameters": {
                "type": "object",
                "properties": {
                    "save_to_disk": {"type": "boolean", "description": "If true, saves directly to user's Pictures/Willy folder."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "open_camera_app",
            "description": "Launch the Windows Camera application.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]

# Custom Tools dynamic storage
CUSTOM_TOOLS: Dict[str, Callable[[Dict[str, Any]], Dict[str, Any]]] = {}
CUSTOM_TOOL_DEFINITIONS: List[Dict[str, Any]] = []
TOOL_DEFINITIONS: List[Dict[str, Any]] = list(BUILTIN_TOOL_DEFINITIONS)


def load_custom_tools() -> int:
    """
    Discovers, imports, and registers all custom tools from willy/tools/custom/.
    """
    global CUSTOM_TOOLS, CUSTOM_TOOL_DEFINITIONS, TOOL_DEFINITIONS
    custom_dir = os.path.join(os.path.dirname(__file__), "custom")
    if not os.path.isdir(custom_dir):
        return len(BUILTIN_TOOL_DEFINITIONS)

    CUSTOM_TOOLS.clear()
    CUSTOM_TOOL_DEFINITIONS.clear()

    pattern = os.path.join(custom_dir, "*.py")
    for py_file in glob.glob(pattern):
        module_name = os.path.splitext(os.path.basename(py_file))[0]
        if module_name.startswith("__"):
            continue

        try:
            spec = importlib.util.spec_from_file_location(f"willy.tools.custom.{module_name}", py_file)
            if spec and spec.loader:
                mod = importlib.util.module_from_spec(spec)
                sys.modules[spec.name] = mod
                spec.loader.exec_module(mod)

                tool_def = getattr(mod, "TOOL_DEF", None)
                run_fn = getattr(mod, "run", None)

                if tool_def and callable(run_fn):
                    fn_name = tool_def.get("function", {}).get("name", module_name)
                    CUSTOM_TOOLS[fn_name] = run_fn
                    CUSTOM_TOOL_DEFINITIONS.append(tool_def)
        except Exception as e:
            print(f"[-] Failed loading custom tool from {py_file}: {e}")

    TOOL_DEFINITIONS.clear()
    TOOL_DEFINITIONS.extend(BUILTIN_TOOL_DEFINITIONS)
    TOOL_DEFINITIONS.extend(CUSTOM_TOOL_DEFINITIONS)

    return len(TOOL_DEFINITIONS)


def reload_tools() -> int:
    """Hot-reloads custom tools and returns total active tools count."""
    return load_custom_tools()


def get_all_tool_definitions() -> List[Dict[str, Any]]:
    """Returns the current list of tool definitions for LLM tool calling."""
    return TOOL_DEFINITIONS


# Initial discovery on module load
load_custom_tools()


def execute_tool(name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    """
    Safely dispatches and executes a tool call by name (both built-in and custom tools).
    """
    try:
        # Check custom tools first
        if name in CUSTOM_TOOLS:
            return CUSTOM_TOOLS[name](arguments)

        # Built-in system tools
        if name == "execute_powershell":
            return execute_powershell(
                command=arguments.get("command", ""),
                timeout=arguments.get("timeout"),
            )
        elif name == "launch_application":
            return launch_application(
                target=arguments.get("target", ""),
                args=arguments.get("args", ""),
            )
        elif name == "open_url":
            return open_url(
                url=arguments.get("url", ""),
            )
        elif name == "interact_ui":
            return interact_ui(
                action=arguments.get("action", ""),
                keys=arguments.get("keys"),
                text=arguments.get("text"),
                coordinates=arguments.get("coordinates"),
            )
        elif name == "scroll_screen":
            return scroll_screen(
                direction=arguments.get("direction", "down"),
                amount=arguments.get("amount", 5),
            )
        elif name == "get_workspace_focus_info":
            from willy.tools.vision_tools import get_workspace_focus_info
            return get_workspace_focus_info()
        elif name == "describe_screen":
            from willy.tools.vision_tools import describe_screen
            return describe_screen(
                question=arguments.get("question"),
                monitor=arguments.get("monitor", "active"),
            )
        elif name == "click_screen_element":
            from willy.tools.vision_tools import click_screen_element
            return click_screen_element(
                target_description=arguments.get("target_description", ""),
                monitor=arguments.get("monitor", "active"),
            )
        elif name == "get_system_status":
            return get_system_status()
        elif name == "volume_control":
            return volume_control(
                action=arguments.get("action", ""),
                level=arguments.get("level"),
            )
        elif name == "file_operations":
            return file_operations(
                action=arguments.get("action", ""),
                path=arguments.get("path", ""),
                content=arguments.get("content"),
            )
        elif name == "search_files":
            from willy.tools.file_search_tools import search_files
            return search_files(
                query=arguments.get("query", ""),
                directory=arguments.get("directory"),
                extension=arguments.get("extension"),
                open_file=arguments.get("open_file", False),
                show_in_explorer=arguments.get("show_in_explorer", False),
            )
        elif name == "open_file_path":
            from willy.tools.file_search_tools import open_file_path
            return open_file_path(path=arguments.get("path", ""))
        # Self-upgrade tools
        elif name == "inspect_self":
            from willy.tools.self_upgrade import inspect_self
            return inspect_self(target=arguments.get("target", "summary"))
        elif name == "install_python_package":
            from willy.tools.self_upgrade import install_python_package
            return install_python_package(package_name=arguments.get("package_name", ""))
        elif name == "create_or_update_tool":
            from willy.tools.self_upgrade import create_or_update_tool
            return create_or_update_tool(
                tool_name=arguments.get("tool_name", ""),
                description=arguments.get("description", ""),
                parameters=arguments.get("parameters", {}),
                python_code=arguments.get("python_code", ""),
            )
        elif name == "edit_willy_source":
            from willy.tools.self_upgrade import edit_willy_source
            return edit_willy_source(
                file_path=arguments.get("file_path", ""),
                new_content=arguments.get("new_content", ""),
            )
        elif name == "reload_willy_tools":
            from willy.tools.self_upgrade import reload_willy_tools
            return reload_willy_tools()
        elif name == "restart_willy":
            from willy.tools.self_upgrade import restart_willy
            return restart_willy(delay_sec=arguments.get("delay_sec", 1.5))
        elif name == "launch_browser_with_profile":
            from willy.tools.browser_tools import launch_browser_with_profile
            return launch_browser_with_profile(
                browser=arguments.get("browser", "edge"),
                profile_query=arguments.get("profile_query"),
                url=arguments.get("url"),
            )
        elif name == "manage_windows_settings":
            from willy.tools.settings_tools import manage_windows_settings
            return manage_windows_settings(
                action=arguments.get("action", "open"),
                page=arguments.get("page"),
                value=arguments.get("value"),
            )
        elif name == "analyze_interaction_logs":
            from willy.tools.log_analyzer import analyze_interaction_logs
            return analyze_interaction_logs(limit=arguments.get("limit", 15))
        elif name == "check_app_running":
            from willy.tools.process_tools import check_app_running
            return check_app_running(target=arguments.get("target", ""))
        elif name == "close_app":
            from willy.tools.process_tools import close_app
            return close_app(target=arguments.get("target", ""), force=arguments.get("force", False))
        elif name == "lock_workstation":
            from willy.tools.power_tools import lock_workstation
            return lock_workstation()
        elif name == "unlock_or_wake":
            from willy.tools.power_tools import unlock_or_wake
            return unlock_or_wake(pin=arguments.get("pin"))
        elif name == "shutdown_system":
            from willy.tools.power_tools import shutdown_system
            return shutdown_system(delay_sec=arguments.get("delay_sec", 30))
        elif name == "restart_system":
            from willy.tools.power_tools import restart_system
            return restart_system(delay_sec=arguments.get("delay_sec", 30))
        elif name == "cancel_shutdown":
            from willy.tools.power_tools import cancel_shutdown
            return cancel_shutdown()
        elif name == "check_network_status":
            from willy.tools.network_tools import check_network_status
            return check_network_status()
        elif name == "manage_autostart":
            from willy.tools.autostart_tools import manage_autostart
            return manage_autostart(
                action=arguments.get("action", "status"),
                mode=arguments.get("mode", "ui"),
            )
        elif name == "focus_window":
            from willy.tools.window_tools import focus_window
            return focus_window(target=arguments.get("target", ""))
        elif name == "list_open_windows":
            from willy.tools.window_tools import list_open_windows
            return list_open_windows()
        elif name == "minimize_window":
            from willy.tools.window_tools import minimize_window
            return minimize_window(target=arguments.get("target"))
        elif name == "fetch_web_content":
            from willy.tools.web_tools import fetch_web_content
            return fetch_web_content(
                url=arguments.get("url", ""),
                max_chars=arguments.get("max_chars", 2500),
            )
        elif name == "search_web":
            from willy.tools.web_tools import search_web
            return search_web(
                query=arguments.get("query", ""),
                num_results=arguments.get("num_results", 5),
            )
        elif name == "take_photo":
            from willy.tools.camera_tools import take_photo
            return take_photo(save_to_disk=arguments.get("save_to_disk", False))
        elif name == "open_camera_app":
            from willy.tools.camera_tools import open_camera_app
            return open_camera_app()
        else:
            return {"success": False, "error": f"Tool '{name}' is not registered."}

    except Exception as e:
        return {"success": False, "error": f"Unhandled error in tool '{name}': {str(e)}"}
