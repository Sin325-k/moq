#!/bin/bash

set -Eeuo pipefail

ROOT=/home/shinya/moq/experiment/audio_priority
COMPARISON=$ROOT/comparison_concurrent_3to1to3_runs1-5.txt
MODE=${1:-run}
[[ "$MODE" == run || "$MODE" == --check-resume ]] || {
	echo "usage: $0 [--check-resume]" >&2
	exit 2
}
[[ ! -e "$COMPARISON" ]] || {
	echo "既存ファイルを上書きしません: $COMPARISON" >&2
	exit 1
}

orders=(
	"R A B"
	"A B R"
	"B R A"
	"R B A"
	"B A R"
)

declare -A completed
next_measurement=

validate_existing() {
	local condition=$1
	local run=$2
	local prefix=concurrent_${condition}_3to1to3_run${run}
	local validation_output
	validation_output=$(mktemp)
	if ! "$ROOT/.venv/bin/python" "$ROOT/analyze_concurrent.py" \
		"$condition" "$ROOT/$prefix.csv" "$ROOT/tbf_$prefix.jsonl" \
		"$ROOT/relay_$prefix.txt" "$ROOT/subscriber_$prefix.txt" \
		"$ROOT/publisher_$prefix.txt" >"$validation_output" 2>&1; then
		cat "$validation_output" >&2
		rm -f -- "$validation_output"
		echo "既存成果物の再解析に失敗しました: $prefix" >&2
		return 1
	fi
	if ! grep -q '^validation: .* OK$' "$validation_output"; then
		cat "$validation_output" >&2
		rm -f -- "$validation_output"
		echo "既存成果物にvalidation成功記録がありません: $prefix" >&2
		return 1
	fi
	rm -f -- "$validation_output"
}

for index in "${!orders[@]}"; do
	run=$((index + 1))
	read -r -a conditions <<<"${orders[$index]}"
	for condition in "${conditions[@]}"; do
		prefix=concurrent_${condition}_3to1to3_run${run}
		outputs=(
			"$ROOT/$prefix.csv"
			"$ROOT/relay_$prefix.txt"
			"$ROOT/subscriber_$prefix.txt"
			"$ROOT/publisher_$prefix.txt"
			"$ROOT/tbf_$prefix.jsonl"
			"$ROOT/analysis_$prefix.txt"
		)
		existing=0
		for output in "${outputs[@]}"; do
			[[ ! -e "$output" ]] || ((existing += 1))
		done
		if ((existing == 0)); then
			[[ -n "$next_measurement" ]] || next_measurement="run${run} $condition"
			continue
		fi
		if ((existing != ${#outputs[@]})); then
			echo "成果物が一部だけ存在します。resumeしません: $prefix ($existing/${#outputs[@]})" >&2
			exit 1
		fi
		for output in "${outputs[@]}"; do
			[[ -s "$output" ]] || {
				echo "空の成果物があります。resumeしません: $output" >&2
				exit 1
			}
		done
		validate_existing "$condition" "$run" || exit 1
		completed["$run:$condition"]=1
		echo "validation成功済みとしてskip: run${run} $condition"
	done
done

if [[ "$MODE" == --check-resume ]]; then
	if [[ -n "$next_measurement" ]]; then
		echo "resume確認成功。次の測定: $next_measurement"
	else
		echo "resume確認成功。全測定が完了済みです"
	fi
	exit 0
fi

sudo -n true 2>/dev/null || {
	echo "sudo認証がありません。同じ端末で sudo -v を実行してください" >&2
	exit 1
}

for index in "${!orders[@]}"; do
	run=$((index + 1))
	read -r -a conditions <<<"${orders[$index]}"
	echo "===== run${run}: ${conditions[*]} ====="
	for condition in "${conditions[@]}"; do
		if [[ -n "${completed["$run:$condition"]:-}" ]]; then
			echo "skip run${run} $condition"
			continue
		fi
		RUN_LABEL=run${run} "$ROOT/run_concurrent_pilot.sh" "$condition"
	done
done

"$ROOT/.venv/bin/python" "$ROOT/aggregate_concurrent_runs.py" | tee "$COMPARISON"
