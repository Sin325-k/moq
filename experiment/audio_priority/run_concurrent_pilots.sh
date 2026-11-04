#!/bin/bash

set -Eeuo pipefail

ROOT=/home/shinya/moq/experiment/audio_priority
COMPARISON=$ROOT/comparison_concurrent_3to1to3_pilots.txt
[[ ! -e "$COMPARISON" ]] || {
	echo "既存ファイルを上書きしません: $COMPARISON" >&2
	exit 1
}

for condition in R A B; do
	"$ROOT/run_concurrent_pilot.sh" "$condition"
done

"$ROOT/.venv/bin/python" "$ROOT/compare_concurrent_pilots.py" | tee "$COMPARISON"
