"""
Git for Willy, on the PC and on the server (the same file runs in both places).

    repos()                     every git project under the usual folders, with branch,
                                changes, ahead/behind and last commit
    git_action(action, repo...) status, log, branches, diff, fetch, pull, push, switch,
                                create_branch, restore, stash, stash_pop, commit, clone

Never prompts for credentials (it fails fast instead), never force-pushes, and refuses to
switch branches or pull over uncommitted changes unless asked to stash them first.
The hub decides which actions need the user's yes before they reach this module.
"""

import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

HOME = Path.home()
SKIP = {"node_modules", ".venv", "venv", "__pycache__", "dist", "build", ".cache", ".npm", ".nvm", ".local",
        "AppData", ".pm2", ".willy-trash", "site-packages", ".antigravity-server", ".codex", "Library"}
OUTPUT_LIMIT = 8000
_CACHE: Dict[str, Any] = {"at": 0.0, "repos": []}


def _roots() -> List[Path]:
    if os.name == "nt":
        roots = [HOME / "Desktop", HOME / "Documents", HOME / "source", HOME / "Projects", HOME / "projects"]
        roots += [Path(f"{d}:\\") for d in "DEFG" if Path(f"{d}:\\").exists()]
    else:
        roots = [HOME, Path("/var/www"), Path("/opt"), Path("/srv")]
    extra = os.getenv("WILLY_GIT_ROOTS")
    if extra:
        roots = [Path(p) for p in extra.split(os.pathsep)] + roots
    return [r for r in roots if r.exists()]


def _git(args: List[str], cwd: Path, timeout: float = 60) -> Dict[str, Any]:
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "never",
           "GIT_SSH_COMMAND": os.environ.get("GIT_SSH_COMMAND", "ssh -o BatchMode=yes -o ConnectTimeout=15")}
    try:
        p = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, timeout=timeout, env=env,
                           creationflags=0x08000000 if os.name == "nt" else 0)  # no console window on Windows
        out, err, code = p.stdout, p.stderr, p.returncode
    except subprocess.TimeoutExpired:
        out, err, code = "", f"git {args[0]} timed out after {timeout}s", -1
    except FileNotFoundError:
        out, err, code = "", "git isn't installed here.", -1
    return {"ok": code == 0, "code": code, "out": out.strip()[-OUTPUT_LIMIT:], "err": err.strip()[-2000:]}


def _find(max_depth: int = 4, budget_sec: float = 8.0) -> List[Path]:
    found, seen = [], set()
    deadline = time.monotonic() + budget_sec
    for root in _roots():
        base_depth = len(root.parts)
        for dirpath, dirnames, _files in os.walk(root):
            if time.monotonic() > deadline:
                return found
            here = Path(dirpath)
            if ".git" in dirnames and here not in seen:
                seen.add(here)
                found.append(here)
                dirnames[:] = []  # don't descend into a repo
                continue
            depth = len(here.parts) - base_depth
            dirnames[:] = [d for d in dirnames if d not in SKIP and not d.startswith(("$", ".")) and depth < max_depth]
    return found


def describe(path: Path) -> Dict[str, Any]:
    branch = _git(["rev-parse", "--abbrev-ref", "HEAD"], path, 10)["out"]
    remote = _git(["remote", "get-url", "origin"], path, 10)["out"]
    remote = re.sub(r"https://[^@/]+@", "https://", remote)  # never show tokens embedded in URLs
    status = _git(["status", "--porcelain=v1", "--branch"], path, 20)["out"].splitlines()
    head = status[0] if status else ""
    ahead = int(m.group(1)) if (m := re.search(r"ahead (\d+)", head)) else 0
    behind = int(m.group(1)) if (m := re.search(r"behind (\d+)", head)) else 0
    changes = [line for line in status[1:] if line.strip()]
    last = _git(["log", "-1", "--format=%h %cr: %s"], path, 10)["out"]
    return {"name": path.name, "path": str(path), "branch": branch, "remote": remote,
            "changed_files": len(changes), "ahead": ahead, "behind": behind, "last_commit": last}


def repos(refresh: bool = False) -> Dict[str, Any]:
    if refresh or time.time() - _CACHE["at"] > 600 or not _CACHE["repos"]:
        _CACHE["repos"] = [describe(p) for p in _find()]
        _CACHE["at"] = time.time()
    items = _CACHE["repos"]
    return {"success": True, "repos": items,
            "message": f"{len(items)} git projects: " + ", ".join(f"{r['name']} ({r['branch']})" for r in items[:12])}


def _resolve(repo: str) -> Optional[Path]:
    if not repo:
        return None
    p = Path(os.path.expanduser(repo))
    if p.is_absolute() and (p / ".git").exists():
        return p
    want = re.sub(r"[^a-z0-9]", "", repo.lower())
    known = repos()["repos"]
    exact = [r for r in known if re.sub(r"[^a-z0-9]", "", r["name"].lower()) == want]
    partial = [r for r in known if want and want in re.sub(r"[^a-z0-9]", "", r["name"].lower())]
    hit = (exact or partial or [None])[0]
    return Path(hit["path"]) if hit else None


def git_action(action: str, repo: str = "", branch: str = "", files: Optional[List[str]] = None,
               message: str = "", url: str = "", stash: bool = False, lines: int = 15) -> Dict[str, Any]:
    action = (action or "status").lower()
    if action in ("list", "repos", "list_repos"):
        return repos(refresh=True)
    if action == "clone":
        if not url:
            return {"success": False, "error": "Which repository URL should I clone?"}
        target_root = Path(os.path.expanduser(repo)) if repo else (Path("D:/Project") if os.name == "nt" and Path("D:/Project").exists() else HOME)
        res = _git(["clone", url], target_root, 600)
        _CACHE["at"] = 0
        return {"success": res["ok"], "message": f"Cloned into {target_root}." if res["ok"] else None,
                "error": None if res["ok"] else res["err"]}
    path = _resolve(repo)
    if path is None:
        names = ", ".join(r["name"] for r in repos()["repos"][:15])
        return {"success": False, "error": f"I couldn't find a git project called '{repo}'. Known: {names}."}
    info = describe(path)
    dirty = info["changed_files"] > 0

    def done(res: Dict[str, Any], ok_text: str) -> Dict[str, Any]:
        _CACHE["at"] = 0  # the next listing re-reads this repo
        if res["ok"]:
            return {"success": True, "repo": info["name"], "message": ok_text, "output": res["out"] or res["err"]}
        hint = ""
        if re.search(r"Authentication failed|could not read Username|Permission denied \(publickey\)|terminal prompts disabled", res["err"]):
            hint = " Git couldn't log in to GitHub from here (no saved credentials / SSH key)."
        return {"success": False, "repo": info["name"], "error": (res["err"] or res["out"])[-600:] + hint}

    if action == "status":
        res = _git(["status", "--short", "--branch"], path, 20)
        state = "clean" if not dirty else f"{info['changed_files']} changed files"
        sync = "" if not (info["ahead"] or info["behind"]) else f", {info['ahead']} ahead / {info['behind']} behind origin"
        return {"success": True, **info, "output": res["out"],
                "message": f"{info['name']} is on {info['branch']}, {state}{sync}. Last commit {info['last_commit']}."}
    if action == "log":
        res = _git(["log", f"-{max(1, min(lines, 50))}", "--format=%h %ad %an: %s", "--date=short"], path, 20)
        return done(res, f"Last commits of {info['name']}.")
    if action == "branches":
        _git(["fetch", "--prune", "--quiet"], path, 60)
        res = _git(["branch", "-a", "--format=%(refname:short) %(committerdate:relative)"], path, 20)
        return done(res, f"Branches of {info['name']} (current: {info['branch']}).")
    if action == "diff":
        res = _git(["diff", "--stat"] + (["--"] + files if files else []), path, 20)
        detail = _git(["diff"] + (["--"] + files if files else []), path, 20)
        out = done(res, f"Uncommitted changes in {info['name']}.")
        out["diff"] = detail["out"][-OUTPUT_LIMIT:]
        return out
    if action == "fetch":
        return done(_git(["fetch", "--prune"], path, 120), f"Fetched {info['name']}.")
    if action in ("stash",):
        return done(_git(["stash", "push", "-u", "-m", message or "Willy stash"], path, 60), f"Stashed the changes in {info['name']}.")
    if action in ("stash_pop", "unstash"):
        return done(_git(["stash", "pop"], path, 60), f"Restored the stashed changes in {info['name']}.")
    if action == "pull":
        if dirty and not stash:
            return {"success": False, "error": f"{info['name']} has {info['changed_files']} uncommitted changes; pulling could "
                                               "conflict. Say to stash them first, or commit them."}
        if dirty:
            _git(["stash", "push", "-u", "-m", "Willy: before pull"], path, 60)
        res = _git(["pull", "--ff-only"], path, 300)
        if dirty:
            _git(["stash", "pop"], path, 60)
        return done(res, f"Pulled {info['name']} ({info['branch']}): " + (res["out"].splitlines()[-1] if res["out"] else "up to date") + ".")
    if action in ("switch", "checkout"):
        if not branch:
            return {"success": False, "error": "Which branch?"}
        if dirty and not stash:
            return {"success": False, "error": f"{info['name']} has uncommitted changes; say to stash them to switch."}
        if dirty:
            _git(["stash", "push", "-u", "-m", f"Willy: before switching to {branch}"], path, 60)
        res = _git(["switch", branch], path, 60)
        if not res["ok"] and "invalid reference" in res["err"]:
            _git(["fetch", "--quiet"], path, 60)
            res = _git(["switch", "--track", f"origin/{branch}"], path, 60)
        return done(res, f"{info['name']} is now on {branch}." + (" Your changes are stashed." if dirty else ""))
    if action == "create_branch":
        if not branch:
            return {"success": False, "error": "What should the new branch be called?"}
        return done(_git(["switch", "-c", branch], path, 30), f"Created and switched to {branch} in {info['name']}.")
    if action in ("restore", "discard"):
        target = files or ["."]
        return done(_git(["restore", "--", *target], path, 60),
                    f"Restored {'all files' if target == ['.'] else ', '.join(target)} in {info['name']} to the last commit.")
    if action == "commit":
        if not message:
            return {"success": False, "error": "What should the commit message be?"}
        _git(["add", "-A"] if not files else ["add", "--", *files], path, 60)
        return done(_git(["commit", "-m", message], path, 60), f"Committed to {info['branch']} in {info['name']}: {message}")
    if action == "push":
        res = _git(["push", "-u", "origin", info["branch"]] if info["branch"] != "HEAD" else ["push"], path, 300)
        return done(res, f"Pushed {info['name']} ({info['branch']}) to GitHub.")
    return {"success": False, "error": f"Unknown git action '{action}'."}
