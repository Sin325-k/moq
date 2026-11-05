# MoQ Audio Priority Experiment Handoff

次回のCodexセッションでは、このファイルを最初に最後まで読むこと。
既存CSV、ログ、実験結果を削除または上書きしないこと。

## 現在の到達点

R/A/Bの各5回本測定、A条件でのQUIC stats calibration 5回測定、Minimal adaptive Cの5回測定に加え、Adaptive C RECOVERYの0.8 / 1.0 / 1.2 Mbps各5-run評価が完了した。固定されたローカル実験条件下で帯域ごとの挙動差の再現性を確認した。次の主要課題はAudio保護を維持しながらBackground backlog drainを高速化することで、候補手法はRecovery Boost。任意のネットワークへの一般化は未評価。

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

BはOracle制御であり、RelayがTBFの1 Mbps / 3 Mbps切替時刻をmarkerから知っている。Minimal Cはmarkerを使わずQUIC RTTから輻輳を検出してBackgroundを停止する。Oracle BはCの比較対象として使用する。

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
- RECOVERY CではRTTがbaseline +1 msを3 sample連続して超えるとBackgroundの新規開始を停止する。150 msのRTT安定後に60→40→20 msのcontrolled probeを行い、各stageを最低300 ms維持する。probe失敗後は300 ms cooldownを設ける。TBF markerは判定に使わない

## Adaptive C RECOVERY 5-run再現性

固定3→1→3 Mbps条件の`pilot1`と`run2`～`run5`を完了した。rawのQUIC CSV、TBF JSONL、Subscriber CSV、Relayログから再計算し、各runの保存済みanalysisと5回集計`experiment/audio_priority/comparison_adaptive_c_recovery_runs1-5.txt`に完全一致した。条件と手順は`experiment/audio_priority/ADAPTIVE_C_RECOVERY.md`を参照。値は5回平均 ± run間の標本標準偏差。

- NORMAL→CONGESTED検出: 5/5回、1 Mbps切替から71.369 ± 11.332 ms（最小58.710、最大81.355 ms）
- 1 Mbps中: 各runでRECOVERYに3回入り、probe後に3回CONGESTEDへ戻った。3 Mbps復帰前のfalse NORMALは全runで0
- 3 Mbps復帰後: 全runでRECOVERY Stage 1→2→3→NORMAL。NORMAL復帰まで1050.569 ± 28.065 ms
- 1 Mbps Audio mean: 4.157 ± 0.244 ms、p95: 14.361 ± 2.049 ms。Audio受信は5/5回で400/400
- Background受信: 5/5回で400/400。3 Mbps復帰後のbacklog解消は7624.326 ± 85.455 ms

1.0 Mbpsの全runの状態遷移は`NORMAL→CONGESTED→(RECOVERY Stage 1→CONGESTED)×3→RECOVERY Stage 1→Stage 2→Stage 3→NORMAL`だった。この状態遷移の再現性は固定3→1→3 Mbps条件で確認した。0.8 / 1.2 Mbpsの結果は次節を参照。実ネットワークでの再現性は未評価。

## Adaptive C帯域頑健性の5-run評価

制御設定を固定し、3→0.8→3 Mbps（D1）、既存の3→1.0→3 Mbps（Recovery C）、3→1.2→3 Mbps（D2）を各5 run評価した。D1/D2はrawのQUIC CSV、TBF JSONL、Subscriber CSV、Relayログから再解析し、保存済みanalysisおよび5-run comparisonと一致した。値は5回平均 ± run間の標本標準偏差。詳細は`experiment/audio_priority/ADAPTIVE_C_BANDWIDTH.md`と`experiment/audio_priority/comparison_adaptive_c_bandwidth_0p8_1p0_1p2.txt`を参照。

| ボトルネック | 検出 ms | Audio mean / p95 ms | ボトルネック中 | false NORMAL | 3 Mbps復帰→NORMAL ms | backlog drain ms | Audio / Background |
| --- | ---: | ---: | --- | --- | ---: | ---: | --- |
| 0.8 Mbps D1 | 79.310 ± 8.556 | 68.879 ± 1.900 / 92.012 ± 2.072 | RECOVERY 0回、5/5 run | 0/5 | 1735.138 ± 46.466 | 8712.365 ± 156.174 | 全runで各400/400 |
| 1.0 Mbps Recovery C | 71.369 ± 11.332 | 4.157 ± 0.244 / 14.361 ± 2.049 | 各runでRECOVERY 3回 | 0/5 | 1050.569 ± 28.065 | 7624.326 ± 85.455 | 全runで各400/400 |
| 1.2 Mbps D2 | 77.553 ± 14.517 | 2.918 ± 0.149 / 6.881 ± 0.382 | Stage 2到達、5/5 run | 0/5 | 717.784 ± 444.675 | 7005.127 ± 344.944 | 全runで各400/400 |

0.8 Mbpsでは全runで完全受信したがAudio遅延が大きく、1.0 Mbpsでは低帯域中のRECOVERY probeがCONGESTEDへ戻り、1.2 Mbpsでは全runでStage 2へ進んだ。固定されたローカル実験条件下でこの帯域差を再現した。任意のネットワーク経路で同じ結果になるとは結論しない。

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

calibrationで選んだNORMAL→CONGESTED判定は`RTT > baseline median +1 ms`の3 sample連続。Minimal Cでは初期1秒のRTT medianをbaselineとして、この判定を実装・5回検証した。estimated send rateは現在のweb-transport-quinnでcwndとRTTから計算されるため、独立した混雑証拠ではなく補助指標として扱う。

## Minimal adaptive C 5回測定結果

`pilot1`と`run2`～`run5`を完了した。詳細な各run値とvalidationは`experiment/audio_priority/comparison_adaptive_c_runs1-5.txt`、実験条件とMinimal Cの動作は`experiment/audio_priority/ADAPTIVE_C.md`を参照。値は5回平均 ± run間の標本標準偏差。

- NORMAL→CONGESTED: 5/5回で成功。1 Mbps切替から71.987 ± 12.980 ms（最小57.366、最大81.900 ms）
- Backgroundの新規Group開始停止: 5/5回で検出と同じtick
- 1 Mbps区間のAudio mean: 3.425 ± 0.047 ms
- 1 Mbps区間のAudio p95: 13.067 ± 0.218 ms
- 1 Mbps区間のAudio max: 25.780 ± 0.440 ms
- Audio受信: 5/5回とも400/400、100%
- AのAudio mean 81.101 msからCの3.425 msへ95.78%減少。Oracle Bのmeanは2.113 ms
- Background受信率: 25.150 ± 0.137%。Minimal CはRECOVERY未実装のため、Bとの完了時間比較はできない

RelayはTBF marker・現在帯域・切替時刻を判定に使用しない。TBF JSONLはoffline解析だけで使用する。

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
- Minimal Cの条件と結果: `experiment/audio_priority/ADAPTIVE_C.md`
- Minimal Cの1 run実行: `experiment/audio_priority/run_adaptive_c_pilot.sh`
- Minimal Cの5 run実行とresume: `experiment/audio_priority/run_adaptive_c_measurements.sh`
- Minimal Cのrun validation: `experiment/audio_priority/analyze_adaptive_c.py`
- Minimal Cの5 run集計: `experiment/audio_priority/aggregate_adaptive_c_runs.py`
- Minimal Cの5回集計結果: `experiment/audio_priority/comparison_adaptive_c_runs1-5.txt`
- RECOVERY Cの条件と手順: `experiment/audio_priority/ADAPTIVE_C_RECOVERY.md`
- RECOVERY Cの1 run実行: `experiment/audio_priority/run_adaptive_c_recovery_pilot.sh`
- RECOVERY Cの5 run実行とresume: `experiment/audio_priority/run_adaptive_c_recovery_measurements.sh`
- RECOVERY Cの解析: `experiment/audio_priority/analyze_adaptive_c_recovery.py`
- RECOVERY Cの5 run集計: `experiment/audio_priority/aggregate_adaptive_c_recovery_runs.py`
- RECOVERY Cの5回集計結果: `experiment/audio_priority/comparison_adaptive_c_recovery_runs1-5.txt`
- 帯域頑健性の条件と手順: `experiment/audio_priority/ADAPTIVE_C_BANDWIDTH.md`
- 帯域頑健性の1 run実行: `experiment/audio_priority/run_adaptive_c_bandwidth_pilot.sh`
- 帯域頑健性の5 run実行とresume: `experiment/audio_priority/run_adaptive_c_bandwidth_measurements.sh`
- 帯域頑健性の解析: `experiment/audio_priority/analyze_adaptive_c_bandwidth.py`
- 帯域頑健性の5 run集計: `experiment/audio_priority/aggregate_adaptive_c_bandwidth_runs.py`
- 帯域頑健性の5回集計結果: `experiment/audio_priority/comparison_adaptive_c_bandwidth_0p8_1p0_1p2.txt`

## 次回やること

Adaptive Cは0.8 / 1.0 / 1.2 Mbpsの各5-run評価を完了した。次の主要課題はAudio保護を維持しつつBackground backlog drainを高速化すること。Recovery Boostの設計とpilot実験が候補。既存のrun成果物は削除・上書きしない。

`NORMAL -> CONGESTED -> RECOVERY -> NORMAL`

Background停止後はAudio-only負荷が約0.78 Mbpsとなり、回線が1 MbpsのままでもRTTが低下し得る。RTTがbaseline付近へ戻っただけでは3 Mbps復帰と判定しない。現行RECOVERYでは限定的なBackground probeで容量を確かめ、backlogを一気に流さずAudioを保護する。TBFのrateやmarkerをRelayの制御入力に使わない。Recovery BoostでもAudio保護を評価基準とし、固定ローカル条件での結果を任意のネットワークへ一般化しない。

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
