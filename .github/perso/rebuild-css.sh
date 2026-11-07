#!/bin/sh
# Rebuild src/static/css/main.css with the Tailwind version named in its header,
# so the custom pages' classes are included without restyling the rest of the app.
set -eu

ROOT=$(cd "$(dirname "$0")/../.." && pwd)
CSS="$ROOT/src/static/css/main.css"
VERSION=$(head -1 "$CSS" | sed -E 's/.*tailwindcss v([0-9][0-9.]*).*/\1/')
WORK=$(mktemp -d)

echo "Rebuilding main.css with Tailwind $VERSION"
npm install --silent --prefix "$WORK" "tailwindcss@$VERSION" "@tailwindcss/cli@$VERSION"
sed "s#source(\"../../\")#source(\"$ROOT/src\")#" "$ROOT/src/static/css/input.css" > "$WORK/input.css"
(cd "$WORK" && npx tailwindcss -i input.css -o "$CSS")
rm -rf "$WORK"
