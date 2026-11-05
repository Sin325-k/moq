#!/bin/bash

set -Eeuo pipefail

ROOT=/home/shinya/moq/experiment/audio_priority
[[ $# -ge 1 && $# -le 2 ]] || {
	echo "usage: $0 D1|D2 [--check-resume]" >&2
	exit 2
}

case "$1" in
	D1) CONDITION=D1; BANDWIDTH_LABEL=D1_0p8mbps ;;
	D2) CONDITION=D2; BANDWIDTH_LABEL=D2_1p2mbps ;;
	*) echo "usage: $0 D1|D2 [--check-resume]" >&2; exit 2 ;;
esac
CHECK_ONLY=0
if [[ $# -eq 2 ]]; then
	[[ "$2" == --check-resume ]] || {
		echo "usage: $0 D1|D2 [--check-resume]" >&2
		exit 2
	}
	CHECK_ONLY=1
fi

cd "$ROOT"
[[ -f run_adaptive_c_bandwidth_pilot.sh && -f aggregate_adaptive_c_bandwidth_runs.py ]] || {
	echo "pilot runnerまたはaggregatorがありません" >&2
	exit 1
}

labels=(pilot1 run2 run3 run4 run5)
first_missing=
for label in "${labels[@]}"; do
	prefix=adaptive_c_${BANDWIDTH_LABEL}_${label}
	paths=(
		"quic_${prefix}.csv"
		"tbf_${prefix}.jsonl"
		"publisher_${prefix}.txt"
		"subscriber_${prefix}.txt"
		"subscriber_${prefix}.csv"
		"relay_${prefix}.txt"
		"analysis_${prefix}.txt"
	)
	present=0
	for path in "${paths[@]}"; do
		if [[ -e "$path" ]]; then
			[[ -f "$path" && -s "$path" ]] || {
				echo "空または通常ファイルでない成果物です。保持して停止: $path" >&2
				exit 1
			}
			((present += 1))
		fi
	done
	if ((present == 0)); then
		[[ "$label" != pilot1 ]] || {
			echo "pilot1がありません。既存pilotの保存状態を確認してください" >&2
			exit 1
		}
		[[ -n "$first_missing" ]] || first_missing=$label
		continue
	fi
	((present == ${#paths[@]})) || {
		echo "一部成果物が欠損しています。保持して停止: $prefix ($present/${#paths[@]})" >&2
		exit 1
	}
	[[ -z "$first_missing" ]] || {
		echo "先行runが欠けています。非連続な成果物を保持して停止: $prefix" >&2
		exit 1
	}
	python3 -B aggregate_adaptive_c_bandwidth_runs.py "$CONDITION" --validate-run "$label"
done

summary="comparison_adaptive_c_${BANDWIDTH_LABEL}_runs1-5.txt"
cross=comparison_adaptive_c_bandwidth_0p8_1p0_1p2.txt
if [[ -n "$first_missing" ]]; then
	[[ ! -e "$summary" ]] || {
		echo "run未完了なのに集計が存在します。保持して停止: $summary" >&2
		exit 1
	}
	[[ ! -e "$cross" ]] || {
		echo "run未完了なのに横断比較が存在します。保持して停止: $cross" >&2
		exit 1
	}
	echo "$CONDITION: pilot1からの連続した完成済みrunを検証済み。次は $first_missing"
else
	if [[ -e "$summary" ]]; then
		[[ -f "$summary" && -s "$summary" ]] || {
			echo "集計が空または通常ファイルではありません: $summary" >&2
			exit 1
		}
		python3 -B aggregate_adaptive_c_bandwidth_runs.py "$CONDITION" --output "$summary"
	fi
	echo "$CONDITION: 5 runsすべて再検証済み"
fi

((CHECK_ONLY == 0)) || exit 0

if [[ -n "$first_missing" ]]; then
	sudo -n true 2>/dev/null || {
		echo "sudo認証がありません。同じ端末で sudo -v を実行してください" >&2
		exit 1
	}
	start=0
	for label in "${labels[@]}"; do
		[[ "$label" == "$first_missing" ]] && start=1
		((start == 1)) || continue
		echo "$CONDITION $label を開始します"
		RUN_LABEL="$label" ./run_adaptive_c_bandwidth_pilot.sh "$CONDITION"
		python3 -B aggregate_adaptive_c_bandwidth_runs.py "$CONDITION" --validate-run "$label"
	done
fi

python3 -B aggregate_adaptive_c_bandwidth_runs.py "$CONDITION" --output "$summary"

other_summary=comparison_adaptive_c_D1_0p8mbps_runs1-5.txt
[[ "$CONDITION" == D2 ]] || other_summary=comparison_adaptive_c_D2_1p2mbps_runs1-5.txt
if [[ -s "$other_summary" ]]; then
	python3 -B aggregate_adaptive_c_bandwidth_runs.py --compare --output "$cross"
else
	echo "横断比較はD1/D2両方の5-run集計後に作成します"
fi
