#!/bin/bash

set -Eeuo pipefail

ROOT=/home/shinya/moq/experiment/audio_priority
COMPARISON=$ROOT/comparison_adaptive_c_recovery_runs1-5.txt
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
	"$ROOT/.venv/bin/python" "$ROOT/aggregate_adaptive_c_recovery_runs.py" --root "$ROOT" --validate-run "$label"
}

for label in "${labels[@]}"; do
	prefix=adaptive_c_recovery_C_$label
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
			echo "pilot1成果物がありません。停止します" >&2
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
	[[ -z "$next_run" ]] || {
		echo "未実施runより後の成果物が存在します。上書きせず停止します: $label" >&2
		exit 1
	}
	validate_run "$label" || {
		echo "既存runの再解析・validation失敗。停止します: $label" >&2
		exit 1
	}
	completed["$label"]=1
	echo "検証済みとしてskip: $label"
done

if [[ -e "$COMPARISON" ]]; then
	[[ -z "$next_run" ]] || {
		echo "集計結果が存在するのに未実施runがあります。停止します: $COMPARISON" >&2
		exit 1
	}
	regenerated=$("$ROOT/.venv/bin/python" "$ROOT/aggregate_adaptive_c_recovery_runs.py" --root "$ROOT")
	[[ "$regenerated" == "$(cat "$COMPARISON")" ]] || {
		echo "既存集計結果が再集計と一致しません。上書きせず停止します: $COMPARISON" >&2
		exit 1
	}
	echo "5測定と集計結果は検証済みです。既存ファイルは変更しません"
	exit 0
fi

if [[ "$MODE" == --check-resume ]]; then
	echo "resume確認成功。次の測定: ${next_run:-なし。5測定完了、集計可能}"
	exit 0
fi

if [[ -z "$next_run" ]]; then
	"$ROOT/.venv/bin/python" "$ROOT/aggregate_adaptive_c_recovery_runs.py" --root "$ROOT" --output "$COMPARISON"
	exit 0
fi

sudo -n true 2>/dev/null || {
	echo "sudo認証がありません。同じ端末で sudo -v を実行してください" >&2
	exit 1
}

for label in run2 run3 run4 run5; do
	[[ -z "${completed[$label]:-}" ]] || continue
	RUN_LABEL="$label" "$ROOT/run_adaptive_c_recovery_pilot.sh"
	validate_run "$label" || {
		echo "新規runのvalidation失敗。次のrunへ進みません: $label" >&2
		exit 1
	}
done

"$ROOT/.venv/bin/python" "$ROOT/aggregate_adaptive_c_recovery_runs.py" --root "$ROOT" --output "$COMPARISON"
