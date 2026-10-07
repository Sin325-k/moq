# MoQ Audio Priority 実験計画

## 1. 研究目的

Audio TrackとBackground Trackを同時に送信した場合に、
Subscriberが指定するPriorityによって、優先したTrackの
配信遅延が変化するかを確認する。

## 2. 実験の基本方針

- PublisherとSubscriberはPythonの`moq-rs`で実装する
- Relayは公式の`moq-relay`を使用する
- 最初はMoQやQuinnの公式コードを変更しない
- 実験コードは`experiment/audio_priority`に置く
- 未実行の内容を実測結果として記載しない
- 各段階の完全性を確認してから次へ進む

## 3. 実験段階

| Phase | 内容 | 状態 |
|---:|---|---|
| 0 | 環境とPython APIの確認 | 未着手 |
| 1 | 1 Trackの最小送受信 | 未着手 |
| 2 | AudioとBackgroundの2 Track送受信 | 未着手 |
| 3 | sequence番号と時刻の記録 | 未着手 |
| 4 | Priority指定の確認 | 未着手 |
| 5 | UDP Shaperの追加 | 未着手 |
| 6 | Priority OFF/ONの比較 | 未着手 |
| 7 | queue-depth sweep | 未着手 |
| 8 | fixed-cwnd実験 | 未着手 |
| 9 | CUIfy・Unity統合 | 未着手 |

## 4. 現在の作業

### Phase 0：環境確認

確認する項目：

- [ ] GitHubの取得元を確認する
- [ ] 現在のブランチを確認する
- [ ] commit IDを記録する
- [ ] `import moq`が成功する
- [ ] `moq-relay`を起動できる
- [ ] 公式サンプルのAPIを確認する

### Phase 1：1 Trackの最小送受信

予定している構成：

```text
Python Publisher
      ↓
local moq-relay
      ↓
Python Subscriber
