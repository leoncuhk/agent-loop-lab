#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
task_id="${1:-d03}"
output_dir="${2:-runtime/demo-$(date -u +%Y%m%dT%H%M%SZ)}"
task_split="dev"
if [[ "$task_id" == h* ]]; then task_split="confirmation"; fi

cd "$project_root"
exec uv run --frozen loop-lab run \
  --output "$output_dir" --split "$task_split" --tasks "$task_id" \
  --variants tools --reps 1 --jobs 1 --max-calls 12 --max-tokens 200000
