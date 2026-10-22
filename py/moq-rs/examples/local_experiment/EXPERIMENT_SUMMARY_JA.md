# MoQ Subscriber Priority 制御実験 詳細まとめ

この文書は、実験内容を別の担当者や生成AIへ渡し、PowerPoint資料に再構成できるようにまとめた日本語原稿である。数値の出典は同じディレクトリの `RESULTS.md`。途中で判明した測定上の問題を補正した最終結果を優先して記載している。

## 1. 一言で表した結論

MoQのsubscriber priorityは、混雑時にすべての通信を速くする機能ではない。Quinn内部で複数のQUIC streamが同時に送信候補として待っているときに、限られた送信容量をどのstreamへ先に割り当てるかを決める機能である。

実験では次を確認した。

- Subscriberが指定した明示的な優先度は、MoQ publisherからQuinnへ正しい順序で渡っていた。
- Quinn内部に送信待ちがあると、選択したtrackの遅延が低下した。
- パケット損失は必須ではない。損失ゼロ、再送ゼロでも、Quinn内部に競合を作ると選択trackの遅延が改善した。
- 改善は容量の再配分である。選択trackが速くなる一方、非選択trackは遅くなる。
- Quinnがdatagramを外部FIFOへすぐ渡せる場合、外部FIFOの中ではMoQ priorityが見えなくなるため、優先度の効果はほぼ消えた。
- 効果の大きさは、cwnd、経路遅延、offered load、payload size、track間の生成位相によって変わった。
- Subscription orderを逆転しても、明示的な優先度の向きや最終的な効果は変わらなかった。
- Subscriberがgroupを逐次的に読んでいた初期実装には、application内のhead-of-line waitingがあった。groupごとのframe streamを並行して読むように修正すると、選択側の改善と非選択側の悪化という本来の容量再配分が観測された。

## 2. 実験の目的

中心となる問いは、「MoQのSubscriber側で設定するtrack priorityは、どの条件で実際の配信遅延を変えるのか」である。

単純な有効、無効の比較ではなく、次の4点を段階的に検証した。

1. Subscriberで指定した優先度がMoQ内部で失われず、transportまで正しく届くか。
2. 購読順、group size、損失、cwnd、経路遅延、送信位相のうち、何が結果を左右するか。
3. 待ち時間がPublisher、Quinnのpriority-aware scheduler、Quinn送出後の外部FIFOのどこで発生しているか。
4. 観測された差が、受信処理、時計、GSO、stream上限、短い実験時間などによる測定上の見かけではないか。

## 3. 基本の実験構成

### 3.1 通信経路

```text
Python Publisher
  audio track + background track
        |
        v
local moq-relay
  MoQ over QUIC, Quinn backend
        |
        v
user-space UDP proxy / shaper
  rate, one-way delay, drop-tail queueを制御
        |
        v
Python Subscriber
  trackごとの受信時刻をCSVへ記録
```

PublisherとSubscriberは同一ホストで実行した。同じmonotonic clockを使えるため、Publisherでpayloadを作った時刻からSubscriberがframeを読んだ時刻までをapplication-levelの片方向遅延として測定した。

### 3.2 Track

基本の対称条件では、2本のtrackを同じ形にした。

| 項目 | audio | background |
| --- | ---: | ---: |
| payload size | 1,944 bytes | 1,944 bytes |
| 送信周期 | 20 ms | 20 ms |
| nominal load | 約0.778 Mbps | 約0.778 Mbps |
| 内容 | 48 kHz、mono、signed 16-bit PCM。主にzero-valued PCM | deterministic bytes |

合計offered loadは約1.555 Mbpsである。

初期実験ではbackgroundを32,768 bytes、4 Mbpsとしていたが、group size自体が完了時間へ強く影響することが分かった。この交絡を除くため、以後の機構検証では両trackを1,944 bytes、20 msへ対称化した。

### 3.3 Priority mode

Publisher側の`TrackInfo.priority`は両trackで同じ値にした。比較で変更したのはSubscriber側の`Subscription.priority`だけである。

| Mode | audio priority | background priority |
| --- | ---: | ---: |
| off | 0 | 0 |
| audio | 255 | 0 |
| background | 0 | 255 |

値が大きいtrackほど高優先度である。

### 3.4 Payload headerと計測項目

各payloadの先頭には24-byteのexperiment headerを格納した。

- magic: `MLAB`
- version: `1`
- track kind: audioまたはbackground
- reserved field
- sequence number
- Publisherの`time.monotonic_ns()`による送信時刻

SubscriberのCSVには次を記録した。

- track名
- sequence number
- send time
- receive time
- `recv_group()`が返ったwall-clock時刻
- frameを読んだwall-clock時刻
- sendからreceiveまでのlatency
- frame timestamp
- payload bytes

wall-clockはrelay traceとの結合に使い、主な遅延計算にはmonotonic clockを使った。

### 3.5 実験の再現性と完全性を守る工夫

- 両trackのsubscriptionが有効になるまでPublisherは送信を開始しなかった。
- 各runで固有のbroadcast名を使い、relayのlingerや前runの状態が混ざらないようにした。
- 条件の実行順を固定seedでランダム化した。
- SubscriberがCSVをflushした後、別TCP portからPublisherへcompletion ACKを送った。
- ACKは測定対象のMoQ trafficやlatency intervalには含めなかった。
- sequenceの連続性、重複、欠損を確認した。
- 統計では1 frameを独立標本とせず、1 runを独立標本として扱った。
- 主な追試ではrunごとの中央値を作り、20,000回のpercentile bootstrapで95%信頼区間を求めた。

## 4. 実験の進み方と、各段階で分かったこと

## 4.1 初期比較: group sizeが大きな交絡要因だった

最初はaudioが1,944 bytes、backgroundが32,768 bytesだった。1 Mbps、50 ms、100-packet queueのshaperを通し、priority modeとsubscription orderを比較した。

元のサイズでは、小さいaudio groupはequal priorityでも大きなbackground groupの断片の間に完了しやすかった。このためaudio priorityの追加効果が小さく見えた。一方でbackground priorityを付けると、audioのp50が約4秒まで悪化した。

両trackのgroup sizeを1,944 bytesへ揃えると、audio priorityによるaudio p50改善が明確になった。

| Subscription order | off時のaudio p50 | audio priority時 | 改善率 |
| --- | ---: | ---: | ---: |
| audio first | 3,938 ms | 2,189 ms | 44% |
| background first | 3,994 ms | 1,953 ms | 51% |

判断: payload sizeが異なる比較では、自然なgroup完了特性をpriority効果と誤認する。以後は対称trafficを中心に検証した。

## 4.2 深い外部FIFO: 損失ゼロではpriority差が見えなかった

両trackを1,944 bytes、20 msへ揃え、1 Mbps、50 ms one-way delay、1,000-packet queueで測定した。外部queueは短い実験中の超過trafficをすべて吸収でき、proxy lossは0%だった。

- 全modeのp50は706から720 ms。
- selected trackの改善は0.7%未満。
- completion timeも条件間でほぼ同じ。
- application frameはすべて到着した。

解釈: Quinnがdatagramを外部FIFOへ出した後は、FIFOはstream priorityを知らない。遅延の大部分が外部queue内にあると、priority schedulerはその順序を後から変えられない。

## 4.3 Queue-depth sweep: queueを浅くするとpriority効果が増えた

対称traffic、1 Mbps、50 msを維持し、drop-tail queueを1,000、200、100、50、20 packetへ変えた。

| Queue depth | Proxy drop | audio選択時のaudio改善 | background選択時のbackground改善 |
| ---: | ---: | ---: | ---: |
| 1,000 | 0.00% | 0.2%未満 | 0.7%未満 |
| 200 | 約4% | 0.8% | 0.3% |
| 100 | 約24% | 3.2% | 10.1% |
| 50 | 約51% | 14.8% | 24.4% |
| 20 | 約75% | 35.2% | 47.9% |

priorityの効果はlossが増えるほど大きくなった。ただし、この段階では「lossそのものが必要」なのか、「lossとACK遅延がbyte rangeをQuinn内部のschedulerへ戻し、priority-awareな選択機会を増やした」だけなのかを分離できなかった。

また、非選択trackは遅くなった。これはpriorityが全体を高速化するのではなく、共有容量を選択側へ再配分していることを示していた。

## 4.4 Subscription orderの追試

50-packetと20-packetの重損失条件で、audio-firstとbackground-firstを比較した。どちらの順序でも選択trackは大きく改善した。

初期の5-run結果では「2番目に購読したtrackの改善が大きい」ように見えた。しかし、qlogを使った別の5-run系列ではaudio方向が逆転し、20-runの直接計測でも一貫したorder effectにはならなかった。

最終判断:

- 明示的なpriorityはどちらのsubscription orderでも機能する。
- 少数runで見えた改善率の差は再現性がなく、protocol効果として扱えない。
- 修正後Subscriberによる最終比較では、order間の差は0.0%から0.6%で、すべての95%信頼区間が0を含んだ。

## 4.5 MoQからtransportへのpriority伝搬確認

Rustの`moq-net` test transportとproduction lite publisher pathを使い、socket、relay、proxy、congestion controller、lossを除外したfocused testを行った。

2本のstreamを同時にqueueへ残し、最初に`SendStream::set_priority`へ渡された`send_order`をtrack別に記録した。

| Registration order | Mode | Audio send_order | Background send_order |
| --- | --- | ---: | ---: |
| audio first | off | 254 | 255 |
| audio first | audio | 255 | 254 |
| audio first | background | 254 | 255 |
| background first | off | 255 | 254 |
| background first | audio | 255 | 254 |
| background first | background | 254 | 255 |

明示priorityでは、audio priorityなら常にaudioが255、background priorityなら常にbackgroundが255になった。subscription orderに依存したpriorityの入れ替わりや消失はなかった。

equal priorityにはinsertion-orderによるtie-breakがあったが、これは明示priorityの正しさには影響しなかった。

## 4.6 Quinn qlog: 接続全体の状態差では説明できなかった

50-packet重損失条件でQuinn qlogを有効にし、30 runs、6,000 groups、60 QUIC connectionsを記録した。

- qlog lossは約49.2%から49.9%。
- proxy dropも約49.3%から50.0%。
- median RTTは355から367 ms。
- median cwndは約450から455 KB。
- PTO countの最大値は全runで0。
- lossはすべてreordering thresholdによる判定だった。

条件間の接続全体のloss、RTT、cwnd、PTO差は小さく、数百msのtrack latency差を説明できなかった。

qlogの`packet_sent`にはSTREAM frameやstream IDが含まれないため、track単位のscheduler判断までは追えなかった。

## 4.7 Quinn stream schedulerの直接instrumentation

一時的なinstrumentationを入れ、track名、sequence、QUIC stream IDを対応付け、Quinnがscheduleした全STREAM byte rangeを再構成した。条件は1 Mbps、50 ms、50-packet queueの重損失。両subscription order、3 priority mode、各20回、合計120 runsだった。

完全性:

- 24,000 group-to-stream mappingsをすべて取得。
- group streamのscheduled STREAM eventsは166,475件。
- 各groupのunique bytesはencoded sizeと一致。
- control streamは解析から除外。

selected trackのapplication p50改善は、4つのorder、track比較すべてで正だった。

| Order | Selected track | Median improvement | 95% CI |
| --- | --- | ---: | ---: |
| audio first | audio | 19.1% | 8.8%から26.8% |
| background first | audio | 11.9% | 5.3%から20.6% |
| audio first | background | 17.2% | 5.4%から23.3% |
| background first | background | 16.4% | 9.9%から26.4% |

Quinn内部では、selected trackの最終schedule時刻が33%から49%早くなった。selected trackで再scheduleされたbyteの割合は低下し、non-selected trackでは上昇した。

初回送信のFINは通常0.2から0.3 ms以内にscheduleされていた。大きな差はloss feedback後の再送判断で生まれた。したがって、重損失条件での効果はQuinn内部のloss recovery schedulingに局在していた。

## 4.8 `send_window`によるno-loss controlはボトルネックを移せなかった

12,500-byteと6,000-byteの`send_window`を試した。どちらもproxy dropとSTREAM再送は0だったが、遅延の大部分は引き続き外部FIFOに残った。

12,500 bytesは実際の約50 ms feedback intervalでは約2 Mbps相当で、1.555 Mbpsのoffered loadを上回っていた。selected trackの改善は小さく、すべての95%信頼区間が0を含んだ。

`send_window`は未ACKのstream data量を制限するが、egress rate limiterではない。この方法はQuinn内部に十分な送信待ちを作れなかった。

## 4.9 Fixed congestion window: lossなしでもpriorityは機能した

Quinnの通常のcongestion controllerを、一時的なfixed-window controllerへ置き換えた。cwndを5,000 bytesへ固定し、Quinn pacerを約1 Mbpsに制約した。外部linkは100 Mbps、queueは1,000 packetとし、外部FIFOがボトルネックにならないようにした。

条件:

- 両track 1,944 bytes、20 ms。
- 合計offered load約1.555 Mbps。
- cwnd 5,000 bytes。
- 両subscription order。
- 3 priority mode。
- 各20回、合計120 runs。

完全性:

- 24,000 application groupsがすべて到着。
- proxy drop 0。
- retransmitted STREAM bytes 0%。
- 全groupをQUIC streamへ対応付け。

結果:

| Selected track | Off p50 | Selected-priority p50 | 改善 |
| --- | ---: | ---: | ---: |
| audio | 約3,474 ms | 約2,626 ms | 24.4% |
| background | 約3,494 ms | 約2,630 ms | 24.7% |

selected trackは、同じsequenceの相手trackより99%から100%のpairで先にscheduleを完了した。subscription orderによる差は約0.2 percentage point以内だった。

判断: packet lossは必要条件ではない。Quinn内部で、複数streamが同時に送信候補として残ることが十分条件である。

## 4.10 Fixed-cwnd sweep: 効果が消える境界

50 ms downstream delayでcwndを変えた。外部linkは100 Mbps、proxy lossは0、retransmissionも0だった。

| Fixed cwnd | Selected track | Off p50 | Selected p50 | 改善 |
| ---: | --- | ---: | ---: | ---: |
| 10,000 B | audio | 540 ms | 89 ms | 83.5% |
| 10,000 B | background | 526 ms | 91 ms | 82.7% |
| 11,000 B | audio | 80 ms | 70 ms | 12.2% |
| 11,000 B | background | 79 ms | 71 ms | 9.9% |
| 12,500 B | audio | 63 ms | 55 ms | 13.3% |
| 12,500 B | background | 62 ms | 55 ms | 12.4% |
| 20,000 B | audio | 54 ms | 54 ms | 実質0% |
| 20,000 B | background | 54 ms | 55 ms | 実質0% |

20,000 bytesでは両trackが約0.3 msで初回scheduleを完了した。priorityはsub-millisecondの相対順序を変えたが、application latencyは変えなかった。

fine sweepでは12,750、13,000、13,250、13,500 bytesを比較した。

- 12,750 B: p50改善は約6.8%から6.9%。p95改善は約15%。
- 13,000 B: p50改善は0.5%から0.6%。p95には約14%のtail効果が残った。
- 13,250 B: materialかつ対称なp50、p95効果はほぼ消失。
- 13,500 B: 2 msを超えてschedule待ちしたgroupは0%。application効果も消失。

このworkloadではmedian効果の境界は12,750から13,000 bytesの間だった。ただし、これはprotocol定数ではない。RTT、MTU、payload size、control traffic、track phaseで移動する。

## 4.11 Track phase: 1 msの生成時刻差で結果が変わった

cwnd 13,000 bytesで、backgroundのdeadlineだけを1、2、3、4、5、10 ms遅らせた。

- 1 ms遅れたbackgroundへpriorityを付けると、background p50が15.7%改善した。
- 2 ms遅れでは13.5%改善した。
- 3 msではp50のcluster境界により見かけ上12%悪化したが、p95は同じで、広い分布全体の悪化を示す結果ではなかった。
- 4、5、10 msではmaterialな改善がなかった。
- 早く生成されたaudioへpriorityを付けても、1、2 ms条件では追加効果がなかった。すでに自然な順序でaudioが先にscheduleされていたためである。

1 ms遅らせるtrackをaudioへ入れ替えるmirrored controlでも、遅れたaudioの改善は15.8%だった。track名ではなく相対的な生成時刻が原因であることを確認した。

判断: priorityは予約帯域ではなく、同じ瞬間にeligibleなstreamの順序を決める。すでに送出されたbyteをpreemptできない。

## 4.12 Path delayとcwndの3領域

cwndを13,000 bytesへ固定し、downstream delayを25、75、100、150 msへ変えた。後述のconcurrent readerによる修正後結果は次のとおり。

| Delay | Selected audio | Selected background | Non-selected audio | Non-selected background |
| ---: | ---: | ---: | ---: | ---: |
| 25 ms | materialな変化なし | materialな変化なし | materialな変化なし | materialな変化なし |
| 75 ms | 69.7% faster | 70.3% faster | 52.5% slower | 54.6% slower |
| 100 ms | 18.9% faster | 19.1% faster | 14.2% slower | 14.5% slower |
| 150 ms | 12.4% faster | 12.3% faster | 17.2% slower | 17.3% slower |

75 msで最大効果になった理由は、13,000-byte windowの概算rateが1 track分の0.778 Mbpsより大きく、2 track合計1.555 Mbpsより小さかったためである。selected trackは前方を維持でき、超過分をnon-selected trackへ移せた。

100、150 msではwindow rateがさらに小さくなり、selected track自体もbacklogを蓄積したため、相対改善率が下がった。

同じ関係を75 ms固定でcwndを6,000、13,000、20,000 bytesへ変えて確認した。

| Fixed cwnd | Approx. window rate | Selected improvement | Non-selected change |
| ---: | ---: | ---: | ---: |
| 6,000 B | 0.64 Mbps | 約7.1%から7.3% faster | 約24% slower |
| 13,000 B | 1.39 Mbps | 約70% faster | 約53%から55% slower |
| 20,000 B | 2.13 Mbps | materialな変化なし | materialな変化なし |

ここから3つの領域を整理できる。

1. Window rateが1 track分未満: selected trackもbacklogを蓄積する。改善は限定的。
2. Window rateが1 track分以上、合計load未満: selected trackを維持し、non-selected trackへ待ちを移せる。効果が最大。
3. Window rateが合計load以上: 両trackがすぐscheduleされる。priorityは順序を変えてもlatencyを変えない。

## 4.13 Subscriberのhead-of-line waitingを発見し修正

初期Subscriberは、1つのgroupを受け取ると、そのgroupの全frameを読み終えるまで次の`recv_group()`を呼ばなかった。後続groupのdataがすでに届いていても、applicationが前groupを読み終えるまで観測できなかった。

このため、100 msと150 msの一部の結果で「selected trackだけでなくnon-selected trackもoffより速い」という、容量保存では説明しにくい結果が出た。

切り分けで行ったこと:

- 実験時間を0.5、1、2、3秒へ変え、finite-run drainingを除外。
- unidirectional stream limitが十分大きいことを確認。
- Publisher、relay、Subscriberのwall clockを結合し、遅延がrelay downstream側にあることを確認。
- UDP GSOを無効化し、GSOを除外。
- temporary receive instrumentationでQUIC streamの最初のreadable byteを記録。
- 6,000 groupをrelay STREAM event、QUIC receive event、group return、frame readの4境界でjoin。

直接計測では、最初のstream byteはrelay mappingから139から145 msで到着していた。しかし、applicationがmedian groupを要求したのはその1.67から2.32秒後だった。遅延の所在はnetwork ingressではなく、Subscriber application内のper-track head-of-line waitingだった。

修正では、trackからgroupを到着順に受け続け、各groupのframe streamを別async taskで並行readするようにした。

100 ms delay、cwnd 13,000 bytesの修正後結果:

| Mode | Track | p50 | Equal priorityからの変化 |
| --- | --- | ---: | ---: |
| off | audio | 1,889.7 ms | reference |
| off | background | 1,885.1 ms | reference |
| audio priority | audio | 1,525.9 ms | 19.3% faster |
| audio priority | background | 2,162.7 ms | 14.7% slower |
| background priority | audio | 2,151.6 ms | 13.9% slower |
| background priority | background | 1,523.0 ms | 19.2% faster |

修正後は期待どおり、selected trackが速く、non-selected trackが遅くなった。priorityは両方を同時に速くしていなかった。

境界のjoin結果では、relay schedulingがselectedで約1.42秒、equal priorityで約1.77から1.78秒、non-selectedで約2.04から2.05秒だった。最後のSTREAM eventからframe到着までの時間はすべて約103 msで、設定した100 ms downstream delayと処理時間に一致した。

これにより、最終的なpriority効果はrelay側QuinnのSTREAM schedulingにあると直接確認できた。

## 5. 最終的な因果モデル

```text
複数trackのgroupが生成される
        |
        v
複数のQUIC streamが同時に送信可能になる
        |
        v
Quinn内部にcwndまたはpacingによる送信待ちがあるか
        |
        +-- ない --> 両streamをほぼ即時送出 --> priorityの遅延効果なし
        |
        +-- ある --> priority-aware schedulerが次のstreamを選ぶ
                         |
                         +-- selected trackを先に送る
                         +-- non-selected trackは後へ回る
                         |
                         v
                 容量の再配分として遅延差が発生
```

Lossはbyte rangeを再びschedulerの選択対象へ戻すため、priority効果を強く見せる場合がある。しかしfixed-cwnd実験により、lossがなくても内部backlogだけでpriorityが機能することを確認した。

外部FIFOに移ったdatagramはstream priorityを保持していない。したがって、Quinn送出後の深いbufferに遅延が集中すると、priorityの効果は消える。

## 6. PowerPointで強調すべき確定結果

### 最優先で載せる結果

1. Fixed cwnd 5,000 B、loss 0、再送0でも、selected track p50が約24%改善。
2. 100 ms delay、cwnd 13,000 B、concurrent readerではselectedが約19%高速化し、non-selectedが約14%低速化。
3. 75 ms、cwnd 13,000 Bではselectedが約70%高速化し、non-selectedが約53%から55%低速化。
4. 75 ms固定のwindow sweepでは、6,000 Bで約7%、13,000 Bで約70%、20,000 Bで0%。
5. 13,000 B固定のdelay sweepでは、25 msで0%、75 msで約70%、100 msで約19%、150 msで約12%。
6. Subscription order差は0.0%から0.6%で、全95%信頼区間が0を含んだ。
7. Quinn direct traceでselected streamの最終scheduleが33%から49%早まり、重損失時の機構を直接観測。

### 結論として使う表現

- 「priorityは帯域を増やす機能ではなく、制約された送信容量の配分順序を決める機能」
- 「lossはpriority効果の必要条件ではない。内部競合を作る一つの方法にすぎない」
- 「重要なのはqueueの深さだけでなく、そのqueueがpriority-aware schedulerの前にあるか、後にあるか」
- 「効果の境界は固定cwnd値ではなく、bandwidth-delay関係とoffered loadの比率で決まる」
- 「評価ではpayload sizeと生成位相を揃え、group frameを並行readする必要がある」

## 7. PowerPointで注意すべき点

以下は誤った、または現在は限定付きの説明になる。

- 「lossがあるからpriorityが効く」は誤り。lossなしのfixed-cwnd実験でも効果を確認した。
- 「priorityで両trackが速くなる」は誤り。これは旧Subscriberの逐次group readが作った測定artifactだった。
- 「2番目にsubscribeしたtrackほど得をする」は支持されない。少数runでの見かけで、再現しなかった。
- 「cwnd 13,000 Bが普遍的な境界」は誤り。この数値は50 ms、1,944-byte、20 ms周期、同期生成という条件に固有。
- 「selected trackは常に速くなる」は誤り。競合がなければ変化しない。生成位相によって同時にeligibleでなければpriorityは作用できない。
- 「p50だけを見れば十分」は誤り。13,000 Bではp50効果がほぼ消えてもp95効果が約14%残った。

## 8. 実験の限界

- すべて同一ホストのcontrolled local experimentであり、実インターネットの変動を含まない。
- trafficは機構を切り分けるための対称なraw payloadで、実際のvideo/audio burstとは異なる。
- 一時的なfixed congestion controllerとinstrumentationを使った。製品コードには残していない。
- 主にQuinn backendを調べた。他のQUIC implementationやcongestion controllerで同じ境界になるとは限らない。
- 片方向proxy delayであり、対称RTTそのものではない。
- exact byte thresholdはMTU、protocol overhead、ACK timing、control traffic、payload phaseで変化する。

## 9. 実装とvalidation

Repositoryに残した主な実装:

- `publisher.py`: audioとbackgroundの2 trackを生成し、共通scheduleから送信する。
- `subscriber.py`: priority modeとsubscription orderを指定し、groupを到着順に受けながらframe streamを並行readし、CSVへ記録する。
- `README.md`: ローカル実行手順、payload format、CSV列、ACKの説明。
- `RESULTS.md`: 全実験、統計、negative control、measurement artifact、最終結果の記録。

一時的に使用し、最終repositoryから除去したもの:

- local `web-transport-quinn` patch。
- STREAM scheduler instrumentation。
- receive-boundary instrumentation。
- fixed congestion controller。
- Cargo dependency override。

環境ではNix daemonが使えなかったため、既存のCargoとMaturin inputを使ってoffline buildした。validationにはRuff lintとformat、Pyright、Python bytecode compilation、binding import、CLI startup、3-mode smoke runを使った。最後のsmoke runでは150 groupsをすべて受信し、proxy dropは0だった。

## 10. 推奨PowerPoint構成

### Slide 1: タイトル

「MoQ Subscriber Priority 制御実験」

副題: 「どの条件でpriorityが配信遅延を変えるのか」

### Slide 2: 結論を先に

- priorityはQuinnへ正しく届いた。
- lossは必須ではなかった。
- 内部backlogがあるとselected trackが前進した。
- 効果は全体高速化ではなく容量再配分だった。

推奨図: 2本のstreamと1本の狭い送信路。

### Slide 3: 問いと仮説

- priorityは本当に効くか。
- 何が効果を左右するか。
- どの層にqueueがあるか。
- 測定artifactではないか。

### Slide 4: 実験系

Publisher、relay、UDP shaper、Subscriberの4段構成を図示。

補足: 2 track、3 mode、同一ホストmonotonic clock、CSV。

### Slide 5: 公平な比較のための設計

- 両subscription完了後に同時開始。
- unique broadcast。
- randomized condition order。
- completion ACK。
- run-level bootstrap CI。

### Slide 6: 最初の交絡、group size

元の非対称sizeと、対称化後のaudio p50を比較。

メッセージ: 小さいgroupの自然な完了をpriority効果と混同しない。

### Slide 7: 外部FIFOとqueue sweep

横軸queue depth、縦軸selected p50改善率のグラフ。

メッセージ: 深い外部FIFOでは差がなく、浅いqueueと重損失で効果が増えた。

### Slide 8: 効果の場所を追跡

MoQ test transport、qlog、STREAM instrumentationの3段階。

メッセージ: 明示priorityは正しく伝搬し、差はQuinnのstream schedulingにあった。

### Slide 9: 決定的なno-loss実験

fixed cwnd 5,000 B、loss 0、retransmission 0、24,000 groups、selected p50約24%改善。

メッセージ: lossは必要条件ではない。

### Slide 10: 3つのcapacity regime

window rateとloadの関係を3領域で図示。

- 1 track未満。
- 1 track以上、合計未満。
- 合計以上。

75 msの6,000、13,000、20,000 B結果を棒グラフにする。

### Slide 11: Phase sensitivity

同期、1 ms、2 ms、3 ms以上の変化を示す。

メッセージ: priorityは同時にeligibleなstreamだけを並べ替える。

### Slide 12: 測定artifactと修正

修正前の逐次group readと、修正後のconcurrent group readを左右に並べる。

100 ms条件のselected約19% faster、non-selected約14% slowerを載せる。

### Slide 13: 最終因果モデル

内部backlogあり、なし、外部FIFOの3分岐を図示。

### Slide 14: 実運用への示唆

- group frameは並行readする。
- 深い下流FIFOを避ける。
- priority-aware scheduler手前でbackpressureを作る。
- payload sizeと生成位相を揃えて評価する。
- p50とp95を両方見る。

### Slide 15: 限界と次の実験

- 実networkと複数RTT。
- 実際のvideo/audio burst。
- congestion controllerの比較。
- QoEとbandwidth fairnessの同時評価。

## 11. PowerPoint作成担当へ渡す短い指示文

以下の文をこの文書と一緒に渡すとよい。

> 添付の実験まとめをもとに、技術者向け15枚前後のPowerPointを作成してください。結論を先に示し、実験の進展を「仮説、検証、次の疑問」の流れで構成してください。最重要メッセージは、MoQ priorityの効果を決めるのはlossの有無ではなく、priority-awareなQuinn scheduler内部に競合するstreamが残るかどうか、という点です。グラフでは修正後のconcurrent-reader結果を優先し、旧sequential-reader結果を最終結果として使わないでください。数値には実験条件を併記し、13,000 bytesなどの境界値が普遍的なprotocol定数に見えないようにしてください。最後は「Priorityは速くする魔法ではなく、制約された送信容量をどのstreamへ先に配るかを決める仕組み」で締めてください。

