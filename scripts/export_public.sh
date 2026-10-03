#!/usr/bin/env bash
# Builds a clean copy of the project for the public repo: current files only (no git history),
# no personal data, no secrets, no build output. Refuses to finish if a secret-looking value is found.
#   scripts/export_public.sh ../willy-public
set -euo pipefail
OUT="${1:?usage: scripts/export_public.sh <output folder>}"
cd "$(dirname "${BASH_SOURCE[0]}")/.."
[ -e "$OUT" ] && [ -n "$(ls -A "$OUT" 2>/dev/null)" ] && { echo "$OUT is not empty" >&2; exit 1; }
mkdir -p "$OUT"

# Tracked files plus new files that aren't ignored; minus anything personal or generated.
git ls-files -z --cached --others --exclude-standard \
  | grep -zvE '^(server/data/|secrets/|dist/|build/|logs/|mobile_app/build/|\.claude/|mobile_screen\.png|phone_screen\.png|shortcuts/|README\.public\.md)' \
  | grep -zvE '(\.env$|\.env\.[a-z]+$|google-services\.json|firebase-adminsdk|\.jks$|\.keystore$|\.log$)' \
  | while IFS= read -r -d '' f; do
      [ -f "$f" ] || continue
      mkdir -p "$OUT/$(dirname "$f")"; cp -p "$f" "$OUT/$f"
    done
# Examples are meant to be public.
for ex in .env.example server/.env.example pc_client/.env.example; do [ -f "$ex" ] && { mkdir -p "$OUT/$(dirname "$ex")"; cp -p "$ex" "$OUT/$ex"; }; done

mkdir -p "$OUT/docs"
cp README.md "$OUT/docs/DEVELOPMENT.md"
cp README.public.md "$OUT/README.md"
chmod +x "$OUT/install"

echo "Scanning for secrets..."
HITS=$(grep -rIl --exclude=export_public.sh -E '(AIza[0-9A-Za-z_-]{35}|gsk_[0-9A-Za-z]{20,}|sk-[0-9A-Za-z]{30,}|-----BEGIN (RSA |EC )?PRIVATE KEY-----|"private_key_id"|willy-secret-2026|ghp_[0-9A-Za-z]{30,})' "$OUT" || true)
if [ -n "$HITS" ]; then echo "Possible secrets in:" >&2; echo "$HITS" >&2; exit 2; fi
echo "Clean. $(find "$OUT" -type f | wc -l) files in $OUT"
echo "Next: cd $OUT && git init -b main && git add -A && git commit -m 'Willy' && create the public repo and push."
