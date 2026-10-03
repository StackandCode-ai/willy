"""
Which server shell commands Willy may run without asking: only ones that just look.

Anything that writes, installs, deletes, restarts, redirects output or chains commands in
ways we can't check needs the user's "yes" first (the hub enforces it).
"""

import re
import shlex

READ_ONLY = {
    "ls", "cat", "head", "tail", "df", "du", "free", "uptime", "whoami", "hostname", "ps", "top", "grep", "egrep",
    "zgrep", "find", "wc", "date", "nproc", "lsblk", "ss", "netstat", "ip", "stat", "file", "which", "uname", "id",
    "w", "who", "last", "echo", "sort", "uniq", "awk", "cut", "tree", "realpath", "dig", "nslookup", "ping", "pwd",
    "lscpu", "vmstat", "iostat", "journalctl", "env", "printenv", "basename", "dirname", "md5sum", "sha256sum",
    "column", "tr", "less", "more", "jq", "pm2", "docker", "systemctl", "git", "curl", "sed", "nginx", "free",
}
# For tools that can also change things, the sub-commands that only look.
SUBCOMMANDS = {
    "pm2": {"list", "ls", "status", "jlist", "logs", "describe", "show", "info", "prettylist"},
    "docker": {"ps", "images", "stats", "logs", "inspect", "top", "port", "version", "info", "df"},
    "systemctl": {"status", "is-active", "is-enabled", "is-failed", "list-units", "list-timers", "show", "cat"},
    "git": {"status", "log", "diff", "show", "branch", "remote", "rev-parse", "describe", "shortlog", "tag"},
    "nginx": {"-t", "-T", "-v", "-V"},
}
WRITE_FLAGS = {
    "sed": re.compile(r"(^|\s)-(i|-in-place)"),
    "curl": re.compile(r"(^|\s)(-o|-O|-d|-F|-T|-X|--data|--upload-file|--output|--remote-name|--request)\b"),
    "find": re.compile(r"(^|\s)-(delete|exec|execdir|ok|fprint)\b"),
    "top": re.compile(r"^(?!.*-b)"),  # interactive top would hang: only batch mode
    "journalctl": re.compile(r"--(vacuum|rotate|flush)"),
    "docker": re.compile(r"(^|\s)(-f|--follow)\b"),
}


def is_read_only(command: str) -> bool:
    cmd = (command or "").strip()
    if not cmd or re.search(r"[;&<>`]|\$\(|\|\|", cmd):
        return False
    for segment in cmd.split("|"):
        try:
            words = shlex.split(segment)
        except ValueError:
            return False
        if not words:
            return False
        if words[0] == "sudo":
            return False  # anything with sudo changes the system or reads what it shouldn't
        prog = words[0].rsplit("/", 1)[-1]
        if prog not in READ_ONLY:
            return False
        if prog in SUBCOMMANDS:
            sub = next((w for w in words[1:] if not w.startswith("-") or prog == "nginx"), "")
            if sub not in SUBCOMMANDS[prog]:
                return False
        rule = WRITE_FLAGS.get(prog)
        if rule and rule.search(" ".join(words[1:])):
            return False
    return True


DANGEROUS = re.compile(
    r"rm\s+-[a-z]*r[a-z]*f?\s+/(\s|$)|mkfs|dd\s+.*of=/dev/|:\(\)\s*\{|chmod\s+-R\s+777\s+/(\s|$)|>\s*/dev/sd|"
    r"shutdown|poweroff|halt\b|init\s+0", re.I)


def is_destructive(command: str) -> bool:
    """Commands Willy refuses outright, even with a yes (wipe the disk, power off the server)."""
    return bool(DANGEROUS.search(command or ""))
