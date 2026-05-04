#!/usr/bin/env bash
# Save the current pieces/<id>/index.html as the next version BEFORE editing it.
#
# Usage:
#   scripts/snapshot_version.sh <piece_id> ["short description of what's about to change"]
#
# After this runs, the existing pieces/<id>/index.html is preserved as
# pieces/<id>/versions/v<N>/index.html and meta.json's `versions` array gains an
# entry pointing at it. The actual edit you're about to make becomes vN+1 once
# you commit the new index.html — the version history is "I was this before
# the next commit."
#
# This is a *manual* helper for human-driven iteration. The autonomous mutation
# worker creates NEW pieces (new ids) instead of editing existing ones, so it
# doesn't need versioning.

set -euo pipefail
PIECE="${1:?usage: snapshot_version.sh <piece_id> [description]}"
DESC="${2:-snapshot}"
REPO="$(cd "$(dirname "$0")/.." && pwd)"
DIR="$REPO/pieces/$PIECE"

if [ ! -f "$DIR/index.html" ]; then
  echo "no pieces/$PIECE/index.html — nothing to snapshot"
  exit 1
fi
if [ ! -f "$DIR/meta.json" ]; then
  echo "no pieces/$PIECE/meta.json — refusing to snapshot without metadata"
  exit 1
fi

# next version number from existing meta.json
NEXT_N=$(python3 - <<PY
import json, pathlib
m = json.loads(pathlib.Path("$DIR/meta.json").read_text())
versions = m.get("versions", [])
print(len(versions) + 1)
PY
)

VDIR="$DIR/versions/v$NEXT_N"
mkdir -p "$VDIR"
cp "$DIR/index.html" "$VDIR/index.html"

# update meta.json: append entry to versions array
python3 - <<PY
import json, pathlib, datetime, subprocess
p = pathlib.Path("$DIR/meta.json")
m = json.loads(p.read_text())
m.setdefault("versions", [])
# get current commit short — best-effort
try:
    sha = subprocess.check_output(["git","-C","$REPO","rev-parse","--short","HEAD"], text=True).strip()
except Exception:
    sha = ""
m["versions"].append({
    "n": $NEXT_N,
    "snapshotted_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    "based_on_commit": sha,
    "description": "$DESC",
    "path": "versions/v$NEXT_N/index.html"
})
p.write_text(json.dumps(m, indent=2) + "\n")
print(f"snapshotted pieces/$PIECE/index.html → versions/v$NEXT_N/  ($DESC)")
PY
