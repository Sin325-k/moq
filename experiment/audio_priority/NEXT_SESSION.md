# MoQ Audio Priority Experiment Handoff

次回のCodexセッションでは、このファイルを最初に最後まで読むこと。
既存CSV、ログ、実験結果を削除または上書きしないこと。

## 現在の到達点

R/A/Bの各5回本測定と、A条件でのQUIC stats calibration 5回測定が完了した。次は適応制御Cの設計・実装であり、Cはまだ実装していない。

条件:

- R: Audio-only
- A: Audio + Background concurrent、Background抑制なし
- B: Audio + Background concurrent、1 Mbps区間のみ新規Background Group開始を停止するOracle制御

TBF:

- 0～2秒: 3 Mbps
- 2～4秒: 1 Mbps
- 4秒以降: 3 Mbps
- `tc qdisc change`を使用し、qdiscは削除・再作成しない
- burst = 4 Kb、latency = 400 msで全区間固定

Audio / Background:

- 各400 Group。RはAudioのみ
- payload = 1945 bytes
- interval = 20 ms
- Background max in-flight = 1
- Audio priority = 255
- Background priority = 0

Subscriber:

- `groups_as_arrived()`
- 到着Groupを即時drain
- sequenceごとのreorder buffer
- Background `max_age_us = 5_000_000`

全15測定でAudio sequence 0～399を欠落・重複なく受信した。
A/BはBackground sequence 0～399も欠落・重複なく受信した。
Audio受信率はR/A/Bすべて100%。Background受信率はA/Bとも100%。

5回集計結果:

`experiment/audio_priority/comparison_concurrent_3to1to3_runs1-5.txt`

## R/A/B 5回測定結果

値は5回平均 ± run間標準偏差。

### 1 Mbps区間のAudio

R:

- mean = 1.995 ± 0.143 ms
- p95 = 2.572 ± 0.200 ms

A:

- mean = 81.101 ± 1.287 ms
- p95 = 100.443 ± 0.658 ms

B:

- mean = 2.113 ± 0.028 ms
- p95 = 2.631 ± 0.045 ms

paired B-A:

- Audio mean difference = -78.988 ± 1.284 ms
- Audio p95 difference = -97.812 ± 0.661 ms

5/5 runすべてで以下を確認した。

- Aの1 Mbps区間Audio遅延がRより悪化
- BがAよりAudio meanを改善
- BがAよりAudio p95を改善

### Background

A:

- receive rate = 100%
- completion = 8248.811 ± 5.538 ms
- pending at 4s = 69.600 ± 0.490
- backlog drain = 911.600 ± 12.643 ms

B:

- receive rate = 100%
- completion = 8252.366 ± 4.876 ms
- pending at 4s = 96.600 ± 0.490
- backlog drain = 1165.600 ± 3.611 ms

paired B-A Background completion difference:

- +3.554 ± 7.686 ms

4秒以降はBackground backlog解消の影響でAudio遅延が一時的に増加する。
集計上のAudio backlog-window meanはAが10.471 ± 0.144 ms、Bが10.222 ± 0.181 msだった。

## 重要な解釈

今回の5回測定から、輻輳中にBackgroundの新規Group開始を停止することで、Audio遅延をAudio-onlyに近い水準まで改善できることが再現性をもって確認できた。

BはOracle制御であり、RelayがTBFの1 Mbps / 3 Mbps切替時刻をmarkerから知っている。次の研究段階ではTBF markerを制御入力に使わず、QUIC統計からRelay自身が輻輳を検出する適応制御Cを実装する。Oracle BはCの性能上限・比較対象として使用する。

## 以前のparallel実験から採用した結論

parallel=1、2、4は各5回成功した。ただし、これらの実験ではBackgroundはAudio終了後に送信されていたため、Audio遅延差をBackground並列数による因果的な悪化とは扱わない。

採用する結論は次のとおり。

Audio終了後のBackground送信では、max in-flightを1から2、4へ増やしてもBackground完了時間はほぼ改善しなかった。このため、concurrent実験と次段階の基本値はmax in-flight=1とする。parallel=8には進んでいない。

## 現在のRelay実装

`rs/moq-net/src/lite/publisher.rs`:

- Audio Groupは従来どおり即時送信
- Background Groupはsequence順pending queueへ追加
- Background max in-flight=1
- 送信中でなければ最古sequenceから開始
- Oracle Bでは1 Mbps marker検出後、新規Background Group開始を停止
- 抑制中も到着Groupをpending queueへ保存
- すでに開始済みの1 Groupは完了させる
- 3 Mbps復帰marker検出後、最古sequenceから再開
- marker観測遅延、4秒時点pending、backlog解消時間をログへ記録
- calibration時はRelay→Subscriber接続のQUIC statsを目標10 ms周期でCSVへ記録。TBF markerはRelayに渡さず、Background送信抑制もしない

## QUIC stats calibration 5回測定結果

既存A条件と同じAudio + Background concurrent、Background max in-flight=1、3→1→3 MbpsのTBF条件で、`pilot1`と`run2`～`run5`を完了した。個別CSVとログはローカルに保存し、Gitには5回集計結果`experiment/audio_priority/comparison_stats_calibration_runs1-5.txt`を保存する。実験条件、ログ形式、解析方法は`experiment/audio_priority/STATS_CALIBRATION.md`を参照。

値は5回平均 ± run間の標本標準偏差。

| 区間 | RTT median | RTT p95 |
| --- | ---: | ---: |
| 0～2秒、3 Mbps | 0.171 ± 0.012 ms | 0.244 ± 0.020 ms |
| 2～4秒、1 Mbps | 83.924 ± 2.863 ms | 92.643 ± 0.902 ms |
| 4秒以降、3 Mbps | 0.181 ± 0.008 ms | 7.966 ± 0.303 ms |

各runの正常区間RTT medianをbaselineとした場合、`RTT > baseline +1 ms`が3 sample連続で成立するまでの時間は69.483 ± 9.911 ms（最小59.241、最大82.295 ms）。正常区間のfalse-positive episodeは0、raw false-positiveは0/1002だった。`+2 ms`の3 sample連続は89.357 ± 9.947 msで、こちらもfalse positiveは0だった。倍率閾値では正常区間のfalse positiveが発生した。

bytes lostとpackets lostは5回とも増加しなかった。3 Mbps復帰後、RTTがbaseline +1 ms以下に戻るまで1107.241 ± 29.597 ms、+2 ms以下まで1045.119 ± 23.902 msかかった。

現時点のNORMAL→CONGESTED判定候補は`RTT > baseline median +1 ms`の3 sample連続。5回の測定では`+2 ms`と同じく誤検出0で、検出は約20 ms早かった。まだ制御閾値としてコードに固定していない。estimated send rateは現在のweb-transport-quinnでcwndとRTTから計算されるため、独立した混雑証拠ではなく補助指標として扱う。

## 今回発生した解析問題と修正

A run1では次の状態になった。

- Background task started = 400
- `serve group complete` = 399
- `serve group task dropped started=true` = 1
- CSV受信 = 400/400

sequence 399はSubscriberでは正常受信・emit済みだったが、購読終了がRelayのcomplete記録より先に発生した。

`analyze_concurrent.py`を修正し、以下の両方を正常なtask終端として扱うようにした。

- `serve group complete`
- CSV受信確認済みの`serve group task dropped started=true`

completeとdropが同じsequenceに存在しても二重計上しない。CSV未受信sequenceのdropは失敗扱いのまま。

`run_concurrent_pilot.sh`も`2>&1 | tee`へ変更し、Python Tracebackをanalysisログへ保存するようにした。

`run_concurrent_measurements.sh`にはresume機能を追加した。必要成果物がすべて存在し、非空で、現在の解析による再validationが成功した条件だけをskipする。一部成果物しかない場合やvalidation失敗時は停止する。`--check-resume`で通信を開始せず確認できる。

## 現在使用する主なファイル

- Relay変更: `rs/moq-net/src/lite/publisher.rs`
- Publisher: `experiment/audio_priority/publisher_concurrent.py`
- Subscriber: `experiment/audio_priority/subscriber_concurrent.py`
- TBF制御: `experiment/audio_priority/tbf_schedule.py`
- 1 run実行: `experiment/audio_priority/run_concurrent_pilot.sh`
- 5 run実行とresume: `experiment/audio_priority/run_concurrent_measurements.sh`
- run validation: `experiment/audio_priority/analyze_concurrent.py`
- 5 run集計: `experiment/audio_priority/aggregate_concurrent_runs.py`
- 5 run集計結果: `experiment/audio_priority/comparison_concurrent_3to1to3_runs1-5.txt`
- calibration 1 run実行: `experiment/audio_priority/run_stats_calibration_pilot.sh`
- calibration 5 run実行とresume: `experiment/audio_priority/run_stats_calibration_measurements.sh`
- calibration解析: `experiment/audio_priority/analyze_stats_calibration.py`
- calibration集計: `experiment/audio_priority/aggregate_stats_calibration_runs.py`
- calibration 5回集計結果: `experiment/audio_priority/comparison_stats_calibration_runs1-5.txt`

## 次回やること

適応制御Cのコードはまだ実装していない。次回は今回の検出候補と、3 Mbps復帰後もRTTが約1秒高く残る結果を踏まえ、以下の状態機械とBackground送信再開条件を設計する。

`NORMAL -> CONGESTED -> RECOVERY -> NORMAL`

RECOVERYではBackground backlogを一気に送信してAudioを再度悪化させない方法も検討する。TBFのrateやmarkerをRelayの制御入力に使わない。Cの実装と実通信実験は、このcalibration結果をGitへ保存した後の別作業とする。

## 環境上の重要事項

実験Python環境`.venv`で確認済みのインストール版:

- moq-rs 0.5.1
- moq-ffi 0.4.12
- 内部moq-net 0.3.10

実験Python側の挙動を調査するときは、worktree内の最新Rustコードだけで判断せず、実際にロードされている依存バージョンを再確認すること。

## Git / Branch

- Repository: `~/moq`
- Branch: `relay-background-defer-all`
- push先: `origin`
- `upstream`へはpushしない
