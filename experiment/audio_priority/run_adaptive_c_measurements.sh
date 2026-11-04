#!/bin/bash

set -Eeuo pipefail

ROOT=/home/shinya/moq/experiment/audio_priority
COMPARISON=$ROOT/comparison_adaptive_c_runs1-5.txt
MODE=${1:-run}
[[ $# -le 1 && ( "$MODE" == run || "$MODE" == --check-resume ) ]] || {
	echo "usage: $0 [--check-resume]" >&2
	exit 2
}

labels=(pilot1 run2 run3 run4 run5)
declare -A completed
next_run=

validate_run() {
	local label=$1
	local prefix=adaptive_c_C_$label
	local quic=$ROOT/quic_$prefix.csv
	local tbf=$ROOT/tbf_$prefix.jsonl
	local subscriber_csv=$ROOT/subscriber_$prefix.csv
	local analysis=$ROOT/analysis_$prefix.txt
	local result
	if ! result=$("$ROOT/.venv/bin/python" "$ROOT/analyze_adaptive_c.py" \
		"$quic" "$tbf" "$subscriber_csv" 2>&1); then
		echo "$result" >&2
		echo "既存成果物の再解析に失敗しました: $label" >&2
		return 1
	fi
	if [[ "$result" != *"validation: C transition and Audio delivery OK"* ]] || \
		[[ "$result" != "$(cat "$analysis")" ]]; then
		echo "解析のvalidation記録がありません: $label" >&2
		return 1
	fi
	if ! grep -q 'adaptive C NORMAL to CONGESTED' "$ROOT/relay_$prefix.txt" || \
		grep -Eq 'background (one-mbps phase|three-mbps recovery) started' "$ROOT/relay_$prefix.txt"; then
		echo "C遷移記録がないか、RelayがOracle markerを参照しました: $label" >&2
		return 1
	fi
}

for label in "${labels[@]}"; do
	prefix=adaptive_c_C_$label
	outputs=(
		"$ROOT/quic_$prefix.csv"
		"$ROOT/tbf_$prefix.jsonl"
		"$ROOT/publisher_$prefix.txt"
		"$ROOT/subscriber_$prefix.txt"
		"$ROOT/subscriber_$prefix.csv"
		"$ROOT/relay_$prefix.txt"
		"$ROOT/analysis_$prefix.txt"
	)
	existing=0
	for output in "${outputs[@]}"; do
		[[ ! -e "$output" ]] || ((existing += 1))
	done
	if ((existing == 0)); then
		[[ "$label" != pilot1 ]] || {
			echo "既存pilot1の成果物がありません。停止します" >&2
			exit 1
		}
		[[ -n "$next_run" ]] || next_run=$label
		continue
	fi
	if ((existing != ${#outputs[@]})); then
		echo "成果物が一部だけ存在します。上書きせず停止します: $label ($existing/${#outputs[@]})" >&2
		exit 1
	fi
	for output in "${outputs[@]}"; do
		[[ -s "$output" ]] || {
			echo "空の成果物があります。上書きせず停止します: $output" >&2
			exit 1
		}
	done
	validate_run "$label" || exit 1
	completed["$label"]=1
	echo "validation成功済みとしてskip: $label"
done

if [[ -e "$COMPARISON" ]]; then
	echo "集計結果が既に存在します。上書きせず停止します: $COMPARISON" >&2
	exit 1
fi

if [[ "$MODE" == --check-resume ]]; then
	if [[ -n "$next_run" ]]; then
		echo "resume確認成功。次の測定: $next_run"
	else
		echo "resume確認成功。5測定が完了済みで集計可能です"
	fi
	exit 0
fi

sudo -n true 2>/dev/null || {
	echo "sudo認証がありません。同じ端末で sudo -v を実行してください" >&2
	exit 1
}

for label in run2 run3 run4 run5; do
	if [[ -n "${completed[$label]:-}" ]]; then
		continue
	fi
	RUN_LABEL="$label" "$ROOT/run_adaptive_c_pilot.sh"
	validate_run "$label" || exit 1
done

"$ROOT/.venv/bin/python" "$ROOT/aggregate_adaptive_c_runs.py" --output "$COMPARISON"
