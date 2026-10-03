"""
Fast Local File Search & Discovery Tools for Willy.
Searches user directories, project folders, and drives for files,
and supports opening files or revealing them in Windows File Explorer.
"""

import os
import subprocess
import datetime
from typing import Dict, Any, List, Optional


COMMON_SEARCH_ROOTS = [
    os.path.expanduser("~/Desktop"),
    os.path.expanduser("~/Documents"),
    os.path.expanduser("~/Downloads"),
    os.path.expanduser("~/Pictures"),
    os.path.expanduser("~/Videos"),
    r"d:\Project\hari",
    os.path.expanduser("~"),
]

SKIP_FOLDERS = {
    "node_modules",
    ".git",
    "__pycache__",
    ".gemini",
    "venv",
    ".venv",
    "env",
    "AppData",
    "$Recycle.Bin",
    "System Volume Information",
    "Windows",
    "Program Files",
    "Program Files (x86)",
}


def search_files(
    query: str,
    directory: Optional[str] = None,
    extension: Optional[str] = None,
    max_results: int = 5,
    open_file: bool = False,
    show_in_explorer: bool = False,
) -> Dict[str, Any]:
    """
    Searches the computer for files matching a keyword, filename, or extension.
    If directory is not specified, searches common user directories (Desktop, Documents, Downloads, Projects).
    Options:
      - open_file: Automatically opens the first matching file in its default app.
      - show_in_explorer: Opens Windows File Explorer with the first matching file selected.
    """
    if not query and not extension:
        return {"success": False, "reply": "Please tell me what file name or keyword to search for."}

    clean_query = query.lower().strip() if query else ""
    clean_ext = extension.lower().strip().lstrip(".") if extension else ""

    # Determine base search directories
    if directory:
        target_dir = os.path.expandvars(os.path.expanduser(directory.strip()))
        if os.path.exists(target_dir):
            search_roots = [target_dir]
        else:
            search_roots = [r for r in COMMON_SEARCH_ROOTS if os.path.exists(r)]
    else:
        search_roots = [r for r in COMMON_SEARCH_ROOTS if os.path.exists(r)]

    matches: List[Dict[str, Any]] = []
    seen_paths = set()

    for root_dir in search_roots:
        if len(matches) >= max_results:
            break

        for current_root, dirs, files in os.walk(root_dir):
            # Prune skipped or heavy directories in-place
            dirs[:] = [
                d for d in dirs
                if not d.startswith(".") and d not in SKIP_FOLDERS and not d.endswith(".tmp")
            ]

            for file_name in files:
                f_lower = file_name.lower()

                # Check extension if specified
                if clean_ext and not f_lower.endswith(f".{clean_ext}"):
                    continue

                # Check keyword match
                if clean_query and clean_query not in f_lower:
                    continue

                full_path = os.path.join(current_root, file_name)
                norm_path = os.path.normpath(full_path)
                if norm_path in seen_paths:
                    continue
                seen_paths.add(norm_path)

                try:
                    stat = os.stat(full_path)
                    size_bytes = stat.st_size
                    if size_bytes < 1024:
                        size_str = f"{size_bytes} B"
                    elif size_bytes < 1024 * 1024:
                        size_str = f"{size_bytes / 1024:.1f} KB"
                    else:
                        size_str = f"{size_bytes / (1024 * 1024):.1f} MB"

                    mtime = datetime.datetime.fromtimestamp(stat.st_mtime).strftime("%b %d, %Y")
                except Exception:
                    size_str = "Unknown"
                    mtime = "Unknown"

                # Pretty location label
                parent_folder = os.path.basename(current_root)

                matches.append({
                    "name": file_name,
                    "path": full_path,
                    "folder": parent_folder,
                    "size": size_str,
                    "modified": mtime,
                })

                if len(matches) >= max_results:
                    break

            if len(matches) >= max_results:
                break

    if not matches:
        term = f"'{clean_query}'" if clean_query else f"*.{clean_ext}"
        return {
            "success": True,
            "query": query,
            "matches": [],
            "reply": f"I couldn't find any files matching {term} in your common folders.",
        }

    # Format natural spoken response
    opened_msg = ""
    first_match = matches[0]

    if open_file:
        try:
            os.startfile(first_match["path"])
            opened_msg = f" I have opened {first_match['name']} for you."
        except Exception as e:
            opened_msg = f" (Attempted to open {first_match['name']}, but encountered an error: {e})"

    elif show_in_explorer:
        try:
            subprocess.Popen(["explorer.exe", f"/select,{first_match['path']}"])
            opened_msg = f" I have highlighted {first_match['name']} in File Explorer."
        except Exception:
            pass

    if len(matches) == 1:
        spoken = f"Found {first_match['name']} ({first_match['size']}) in your {first_match['folder']} folder.{opened_msg}"
    else:
        file_list_spoken = ", ".join([f"{m['name']} in {m['folder']}" for m in matches[:3]])
        spoken = f"Found {len(matches)} files matching your search: {file_list_spoken}.{opened_msg}"

    return {
        "success": True,
        "query": query,
        "count": len(matches),
        "matches": matches,
        "reply": spoken,
    }


def open_file_path(path: str) -> Dict[str, Any]:
    """
    Opens any file or folder directly with Windows default program or Explorer.
    """
    clean_path = os.path.expandvars(os.path.expanduser(path.strip().strip('"').strip("'")))
    if not os.path.exists(clean_path):
        return {"success": False, "reply": f"The file or path '{clean_path}' does not exist on your computer."}

    try:
        os.startfile(clean_path)
        base_name = os.path.basename(clean_path)
        return {
            "success": True,
            "path": clean_path,
            "reply": f"Opened {base_name}.",
        }
    except Exception as e:
        return {"success": False, "error": str(e), "reply": f"Failed to open {clean_path}: {str(e)}"}
