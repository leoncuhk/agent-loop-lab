#!/usr/bin/env bash
# New private run directory only. Pass --live explicitly to spend model quota.
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
lab=$(cd "$here/../../.." && pwd)
rev=fd4d59224ab96b43c6dc6888207c67b3bd5a24cf
ref="$lab/../references/commerce-agents"
out=${1:?usage: run.sh NEW_OUTPUT_DIR [--live]}
mode=${2:-}
[[ -z "$mode" || "$mode" == "--live" ]] || exit 2
mkdir "$out"
out=$(cd "$out" && pwd)
mkdir "$out/src"
git -C "$ref" archive "$rev" | tar -x -C "$out/src"
cd "$out/src"
uv venv "$out/venv" --python 3.12 > "$out/install.log" 2>&1
uv pip install --python "$out/venv/bin/python" -r requirements-dev.txt >> "$out/install.log" 2>&1
py="$out/venv/bin/python"
export PYTHONPATH="$out/src/examples" MERCHANT_REQUIRE_HOST_APPROVAL=1
run_check() {
  local dest=$1 expected=$2 actual=0
  shift 2
  "$@" > "$out/$dest.log" 2>&1 || actual=$?
  echo "$actual" > "$out/$dest.exit"
  tail -n 2 "$out/$dest.log"
  [[ "$actual" -eq "$expected" ]]
}
run_check upstream-pytest 0 "$py" -m pytest -q
run_check upstream-check 0 "$py" scripts/check.py
# Strict policy "apply must check price now" is a known negative result on this commit.
run_check strict-1 1 "$py" "$here/probe.py" "$out/strict-1" --require-current-price-cap
"$py" -c 'import json,sys; x=json.load(open(sys.argv[1]))["stale_price_probe"]; assert not x["property_passed"] and x["actual_move_pct"] == 45' "$out/strict-1/result.json"
if [[ "$mode" == "--live" ]]; then
  run_check live-1 0 "$py" "$here/live.py" "$out/live-1"
  "$py" "$here/audit.py" "$out" > "$out/audit-1.json"
fi
