"""
Admin commands for the machine the hub runs on (used by ./install and by the owner):

    python -m server.cli link-code          one-time code to add a phone or PC (also shown on the dashboard)
    python -m server.cli device-key --type server --id server_myhost --name "My server"
                                            a ready device key (the installer gives it to the server agent)
    python -m server.cli sign-in-link [--url https://hub.example.com]
                                            a browser link that signs you in as the owner
    python -m server.cli users              accounts on this hub.
    python -m server.cli set-ai --provider gemini --key KEY [--model M] [--base-url URL] [--skip-test]
                                            choose the AI the brain uses (tested before it is saved)

These talk to the accounts database directly, so they need the same .env as the hub and
are only available to someone who has shell access to it.
"""

import argparse
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m server.cli", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("link-code")
    dk = sub.add_parser("device-key")
    dk.add_argument("--type", default="server", choices=("pc", "mobile", "server"))
    dk.add_argument("--id", required=True)
    dk.add_argument("--name", default="")
    sl = sub.add_parser("sign-in-link")
    sl.add_argument("--url", default=os.getenv("WILLY_PUBLIC_URL", ""))
    sub.add_parser("users")
    sub.add_parser("ai-ready")
    ai = sub.add_parser("set-ai")
    ai.add_argument("--provider", required=True)
    ai.add_argument("--key", default="")
    ai.add_argument("--model", default="")
    ai.add_argument("--base-url", default="")
    ai.add_argument("--skip-test", action="store_true")
    args = ap.parse_args(argv)

    if args.cmd == "ai-ready":
        from server import ai_config

        return 0 if ai_config.current().ready else 1
    if args.cmd == "set-ai":
        import asyncio

        from server import ai_config

        try:
            cfg = ai_config.candidates(args.provider, args.model, args.key, args.base_url)
        except ValueError as e:
            print(str(e), file=sys.stderr)
            return 2
        if not args.skip_test:
            res = asyncio.run(ai_config.test(cfg))
            if not res["ok"]:
                print(res["error"], file=sys.stderr)
                return 1
        ai_config.save(cfg.provider, cfg.model, args.key, args.base_url)
        print(f"AI set to {cfg.provider} ({cfg.model}).")
        return 0

    from server.accounts import Accounts

    acc = Accounts()
    owner = acc.owner_principal()
    if args.cmd == "link-code":
        res = acc.create_link_code(owner.user_id)
        print(res["code"])
    elif args.cmd == "device-key":
        print(acc.issue_device_key(owner.user_id, args.id, args.type, args.name or args.id))
    elif args.cmd == "sign-in-link":
        from server.config import settings

        token = (settings.WILLY_REMOTE_TOKEN or "").strip()
        if not token:
            print("WILLY_REMOTE_TOKEN is not set in server/.env", file=sys.stderr)
            return 1
        base = (args.url or f"http://localhost:{settings.PORT}").rstrip("/")
        print(f"{base}/#token={token}")  # in the URL fragment: the browser never sends it to a server
    elif args.cmd == "users":
        for u in acc.list_users():
            print(f"{u['email']:40} {u['role']:6} {u['status']:8} {u['device_keys']} device(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
