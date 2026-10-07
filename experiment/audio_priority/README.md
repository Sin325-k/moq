# MoQ Audio Priority Experiment

## 目的

Python版moq-rsを用いて、ローカルRelayを介した
PublisherとSubscriber間のデータ送受信を確認する。

最終的には、一つのBroadcast内にAudio Trackと
Background Trackを作成し、Subscriber側で指定するPriorityが
配信遅延に与える影響を評価する。

## 使用する実装

- Repository: moq-dev/moq
- Python API: py/moq-rs
- Relay: moq-relay
- QUIC implementation: Quinn
- Experiment directory: experiment/audio_priority

## 実験の進め方

1. 1 Trackの最小送受信を確認する
2. AudioとBackgroundの2 Trackを送信する
3. sequence番号、送信時刻、受信時刻を記録する
4. SubscriberからPriorityを指定する
5. UDP Shaperを用いて帯域、遅延、queueを制御する
6. Priority OFFとONを比較する
7. 必要に応じてfixed-cwnd実験を行う
8. MoQ単体実験後にCUIfyとUnityへ統合する

## 現在の段階

- 状態: 未実装
- 現在の目標: 1 Trackの最小送受信
- 帯域制限: 未使用
- Priority比較: 未実施
- CUIfy・Unity統合: 未実施

## 最初の完了条件

- Python PublisherがRelayへ接続できる
- Python SubscriberがRelayへ接続できる
- Publisherがtest Trackへ10個のデータを送信できる
- Subscriberが10個すべて受信できる
- 欠損が0件である
- 重複が0件である

## 注意

計画中の内容を実測済みの結果として扱わない。
実装済み、実行済み、検証済みを区別して記録する。
