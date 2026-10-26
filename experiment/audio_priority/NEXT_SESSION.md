# MoQ Audio Priority Experiment - Handoff

## 現在の実験条件

- TBF: 1500 Kbit
- Audio: 100 Group
- Background: 100 Group
- payload: 1945 bytes
- interval: 20 ms

## 現在のSubscriber

Backgroundはordered iteratorを使わず、

- groups_as_arrived()
- Group到着後すぐにframeをdrain
- 完成データをsequence別にreorder buffer
- 0,1,2,...の順でapplicationへemit
- Backgroundのみ max_age_us=5_000_000

を使用。

Audioは従来方式。

## Relay

現在のBackground最大in-flightは2。

BackgroundはRelayで保留後、sequence順にrelease。
parallel=2では最大2 Groupを同時送信するsliding window方式。

Background Priorityは古いsequenceを優先するよう調整済み。

## 判明した問題

parallel=2で通常のnext_group()を使うと、独立QUIC streamの到着順が前後する。

例:
seq1がseq0より先に到着
→ next_group()がseq1を返す
→ next_sequence=2
→ 後着seq0はskip

また、Background Subscriptionのmax_age_us=0では、
取得済みGroupのframeが未完成の間に新しいpresentation edgeが進むと
Error::Old が発生することを確認。

診断結果:
scope=STREAM
code=52
kind=OLD
message=old

対策としてgroups_as_arrived() + 即時drain + reorder bufferを導入。

## 現在の成功結果

### parallel=1 + reorder buffer

5回すべて成功。

- Audio 100/100
- Background 100/100
- max in-flight = 1
- OLDなし
- Protocol errorなし

CSV:

bg_parallel1_reorder_tbf1500k_run1.csv
bg_parallel1_reorder_tbf1500k_run2.csv
bg_parallel1_reorder_tbf1500k_run3.csv
bg_parallel1_reorder_tbf1500k_run4.csv
bg_parallel1_reorder_tbf1500k_run5.csv

### parallel=2 + reorder buffer

5回すべて成功。

- Audio 100/100
- Background 100/100
- max in-flight = 2
- OLDなし
- Protocol errorなし

CSV:

bg_parallel2_reorder_tbf1500k_run1.csv
bg_parallel2_reorder_tbf1500k_run2.csv
bg_parallel2_reorder_tbf1500k_run3.csv
bg_parallel2_reorder_tbf1500k_run4.csv
bg_parallel2_reorder_tbf1500k_run5.csv

## 次にやること

最初にparallel=1とparallel=2の5回分CSVを比較する。

比較項目:

- Audio平均遅延
- Audio p95
- Audio最大遅延
- Background平均遅延
- Background p95
- Background最大遅延
- Background完了時間
- Audio受信率
- Background受信率
- 各指標の5回平均
- 5回間の標準偏差

比較後、問題なければBackground最大in-flight=4へ進む。

parallel=4でもSubscriber、TBF、payload、interval等は変更せず、
変更するのはBackground最大in-flightだけとする。
