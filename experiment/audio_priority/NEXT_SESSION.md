# MoQ Audio Priority Experiment Handoff

次回のCodexセッションでは、このファイルを最初に最後まで読むこと。
既存CSV、ログ、実験結果を削除または上書きしないこと。

## 現在の到達点

R/A/Bの各5回本測定が完了した。

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

## 次回やること

適応制御Cのコードはまだ実装していない。次回はコード変更前に以下を行う。

1. 現在使用中のmoq-dev/moqと、実験Python環境が実際に使用している依存バージョンを確認
2. Relayから取得可能なQUIC統計を調査
3. RTTを確認
4. estimated send rateを確認
5. bytes / packets sentを確認
6. bytes / packets lostを確認
7. congestion windowを確認
8. bytes in flightを確認
9. その他利用可能な輻輳指標を整理

その後、以下の状態機械による適応制御Cを設計する。

`NORMAL -> CONGESTED -> RECOVERY -> NORMAL`

RECOVERYではBackground backlogを一気に送信してAudioを再度悪化させない方法も検討する。Cをいきなり実装せず、まず利用可能なQUIC統計と状態遷移設計を整理する。

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
