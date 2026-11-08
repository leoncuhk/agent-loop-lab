#!/usr/bin/env bash
# From the lab root. Refuse to overwrite any raw run.
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
ref="${LOOP_LAB_REFERENCES:-$here/../../../../references}/agent-loop-safety"
sha=c504861ac65229a852deb44123210c675a67a9b8
out=${1:?usage: run.sh NEW_DIRECTORY [--live]}
test ! -e "$out"
mkdir -p "$out"
out=$(cd "$out" && pwd)
mkdir -p "$out/native/checkout"
git -C "$ref" archive "$sha" | tar -xf - -C "$out/native/checkout"
python3 -m venv "$out/venv"
py="$out/venv/bin/python"
(
  cd "$out/native/checkout"
  "$py" -B -m unittest discover -s tests -t . -v >"$out/native/unittest.log" 2>&1
  "$py" -B agent_loop.py >"$out/native/demo.log" 2>&1
  "$py" -B examples/agent_redteam_integration.py --flow-dir "$out/native/redteam-flow" >"$out/native/redteam.log" 2>&1
)
"$py" - "$out/native" <<'PY'
import hashlib, json, sys
from pathlib import Path
root = Path(sys.argv[1])
record = {p.stem: {"exit": 0, "log_sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
          for p in sorted(root.glob("*.log"))}
(root / "commands.json").write_text(json.dumps(record, indent=2))
PY
"$py" -B "$here/probes.py" "$out/native/checkout" "$out/probes-v2"
if [[ ${2:-} == --live ]]; then
  "$py" -B "$here/live.py" "$out/native/checkout" "$out/live-v1"
  "$py" -B "$here/audit.py" "$out"
fi
