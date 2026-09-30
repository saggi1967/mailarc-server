#!/usr/bin/env bash
# Baut das mailarc-web-SPA, kopiert es in den Build-Kontext (./web) und baut das
# Server-Image mit eingebettetem Frontend (same-origin: UI unter /, API unter /api).
#
# Ergebnis: mailarc-server:<version aus pyproject.toml> für linux/amd64.
# Danach fürs Zielsystem: docker save … | gzip  →  docker load  →  docker compose up -d
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
WEB_SRC="${WEB_SRC:-$HERE/../mailarc-web}"
PLATFORM="${PLATFORM:-linux/amd64}"
VERSION="$(sed -n -E 's/^version[[:space:]]*=[[:space:]]*"([^"]+)".*/\1/p' "$HERE/pyproject.toml" | head -1)"
[ -n "$VERSION" ] || { echo "Version nicht aus pyproject.toml lesbar"; exit 1; }
[ -d "$WEB_SRC" ] || { echo "mailarc-web nicht gefunden: $WEB_SRC (WEB_SRC=… setzen)"; exit 1; }

echo "==> 1/3  mailarc-web bauen ($WEB_SRC)  — relative API-Basis via .env.production"
( cd "$WEB_SRC" && npm ci && npm run build )

echo "==> 2/3  Bundle in den Build-Kontext kopieren ($HERE/web)"
rm -rf "$HERE/web"
cp -R "$WEB_SRC/dist" "$HERE/web"

echo "==> 3/3  Image bauen: mailarc-server:$VERSION ($PLATFORM)"
docker build --platform "$PLATFORM" -t "mailarc-server:$VERSION" "$HERE"

cat <<EOF

Fertig — Image mailarc-server:$VERSION enthält das Web-UI.
Aufs Zielsystem bringen:
  docker save mailarc-server:$VERSION | gzip > mailarc-server-$VERSION.tar.gz
  scp mailarc-server-$VERSION.tar.gz <user>@microsmart1:~/
  # auf microsmart1:
  docker load -i mailarc-server-$VERSION.tar.gz && docker compose up -d
Danach:  http://microsmart1:9000/  = Web-UI   ·   /api = API   ·   /docs = OpenAPI
EOF
