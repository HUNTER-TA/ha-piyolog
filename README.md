# ha-piyolog — ぴよログ (PiyoLog) for Home Assistant

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=HUNTER-TA&repository=ha-piyolog&category=integration)

育児記録アプリ「ぴよログ」の公式 **データフィード API（JSON / schema v1）** を利用して、取得可能なすべての育児記録を Home Assistant へ定期取得・エンティティ化するカスタムインテグレーションです。

授乳・ミルク・睡眠・おしっこ・うんち・体温・身長・体重・離乳食・メモなど **全 40 種類の記録** を Home Assistant のセンサーとして取り込み、ダッシュボードでの可視化や通知オートメーションに活用できます。

> **免責事項**: 本プロジェクトは非公式のコミュニティ製インテグレーションです。API 仕様は [ぴよログ データフィード公式ガイド](https://www.piyolog.com/app/piyolog/data_feed/ja/) に準拠しています。

---

## 目次

- [主な特徴](#主な特徴)
- [事前準備（ぴよログアプリ側）](#事前準備ぴよログアプリ側)
- [インストール方法](#インストール方法)
- [Home Assistant でのセットアップ](#home-assistant-でのセットアップ)
- [提供エンティティ一覧](#提供エンティティ一覧)
- [サービスアクション (`piyolog.get_records`)](#サービスアクション-piyologget_records)
- [ダッシュボード・オートメーション活用例](#ダッシュボードオートメーション活用例)
- [トラブルシューティング](#トラブルシューティング)
- [開発・テスト](#開発テスト)
- [ライセンス](#ライセンス)

---

## 主な特徴

- **画面（UI）からかんたん・安全に設定**
  - Home Assistant の「デバイスとサービス」からフィード URL を入力するだけで設定完了（YAML 編集不要）。
  - 複数人・複数フィードの登録にも対応しています。
  - フィード URL（秘密トークン）はパスワード入力欄でマスクされ、ログ・診断情報・`unique_id` にも一切出力されません。
  - URL の期限切れや期間変更時は **「再構成」** 機能により、既存のエンティティ ID や履歴を維持したまま新しい URL に差し替えられます。
- **取得期間（`24h` / `3d` / `7d` / `28d`）を自動判定**
  - 無料プラン（24時間）・プレミアムプラン（24時間 / 3日 / 7日 / 28日）のいずれにも対応。
  - URL から取得期間を自動検出し、公式キャッシュ保持時間以上の適切なポーリング間隔を自動設定します。
- **API 仕様に厳密準拠した安定動作**
  - `429`（レート制限）や `5xx` エラー時は **指数バックオフ＋ジッター** で自動的に待機間隔を延長し、復旧後に通常間隔へ戻ります。
  - 通信エラー時に「記録 0 件」と誤認せず、前回正常取得データを保持しつつ診断センサー `feed_status` で異常を検知できます。
  - `schema_version` チェック、未知の `type` / フィールドの安全な無視、省略された数値項目を `0` 扱いしない処理を実装しています。
- **全 40 種類の記録と当日／直近 24 時間集計を網羅**
  - 授乳（母乳・ミルク・搾母乳・搾乳）、睡眠中判定・当日睡眠時間（日付またぎ対応）、排泄詳細（うんちの量・かたさ・色）、身体測定（体温・体重・身長・頭囲・胸囲・足左右サイズ）、食事、健康、生活、メモ、カスタム 1〜10 をカバー。
- **DB を肥大化させない履歴取得アクション (`piyolog.get_records`)**
  - メモリ上に保持している最新スナップショットから、追加の API 通信なしで条件に合う記録一覧を取り出せます。

---

## 事前準備（ぴよログアプリ側）

1. ぴよログアプリ（管理権限のあるアカウント）で **「設定」＞「データフィード」＞「新しいフィードを作成」** を開きます。
2. 対象の子ども・取得期間・有効期限を選択してフィードを作成します。
   - **無料プラン**: 24時間（有効期限 90日・最大 1件）
   - **プレミアムプラン**: 24時間 / 3日 / 7日 / 28日（有効期限 90日 / 365日 / 無期限・最大 5件）
3. 作成したフィードの詳細画面で **「フィードURLを表示」** を押し、URL をコピーします。

> **セキュリティ上の注意**
> フィード URL はパスワードと同じ機密情報です。SNS・公開リポジトリ・第三者には共有しないでください。漏えいが疑われる場合はアプリ側でフィードを失効させ、再発行してください。

---

## インストール方法

### 方法 1: HACS（推奨）

#### ワンクリックでリポジトリを追加する
下のボタンをクリックすると、お使いの Home Assistant の HACS 画面でリポジトリ登録ダイアログが開きます。

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=HUNTER-TA&repository=ha-piyolog&category=integration)

#### 手動で HACS に追加する場合
1. Home Assistant で **HACS** を開きます。
2. 右上メニュー（`⋮`）から **「カスタムリポジトリ」** を選択します。
3. 以下を入力して **「追加」** をクリックします：
   - **リポジトリ**: `https://github.com/HUNTER-TA/ha-piyolog`
   - **カテゴリ**: `インテグレーション`（Integration）
4. 一覧から **「PiyoLog (ぴよログ)」** を選択して **「ダウンロード」** をクリックします。
5. **Home Assistant を再起動** します。

### 方法 2: 手動インストール

1. [GitHub リポジトリ](https://github.com/HUNTER-TA/ha-piyolog) からダウンロードし、`custom_components/piyolog` フォルダを Home Assistant の `<config>/custom_components/piyolog` へコピーします。
2. **Home Assistant を再起動** します。

---

## Home Assistant でのセットアップ

### 初回登録
1. Home Assistant の **「設定」＞「デバイスとサービス」＞「統合を追加」** を開きます。
2. **「PiyoLog (ぴよログ)」** を検索して選択します。
3. 以下の項目を入力して送信します：
   - **フィードURL**: ぴよログアプリで発行した URL（`https://feed.piyolog.com/v1/feed/...`）
   - **表示名**: デバイス名（例: `赤ちゃん`、`ぴよログ` など）
   - **更新間隔（秒・省略可）**: 空欄にすると、期間に応じた推奨間隔が自動設定されます。

| 取得期間 | URL 表記 | 最小許容間隔（API キャッシュ時間） | デフォルト更新間隔 |
| --- | --- | --- | --- |
| 24時間 | `24h` | 60秒（1分） | 180秒（3分） |
| 3日 | `3d` | 120秒（2分） | 300秒（5分） |
| 7日 | `7d` | 300秒（5分） | 600秒（10分） |
| 28日 | `28d` | 900秒（15分） | 900秒（15分） |

### 更新間隔の変更（オプション）
統合ページの **「設定」**（歯車アイコン）から、いつでも更新間隔を変更できます（API のキャッシュ保持時間未満の値は指定できないよう自動で保護されます）。

### URL の差し替え（再構成）
フィードの期限切れ・失効・取得期間の変更（例: `24h` → `7d`）で新しい URL を発行した場合は、統合ページのメニュー（`⋮`）＞ **「再構成」** から新しい URL を入力してください。既存のエンティティ ID や履歴を壊さずに切り替えられます。

---

## 提供エンティティ一覧

1 つのフィードにつき 1 つのデバイスが作成され、その配下に以下のエンティティが生成されます。
※エンティティの日本語名・英語名は Home Assistant の表示言語に合わせて自動で切り替わります。

### バイナリセンサー

| キー | 日本語名 | 内容 | 主な属性 |
| --- | --- | --- | --- |
| `sleeping` | 睡眠中 | 最新の睡眠記録が `Sleep` なら `on`、`WakeUp` なら `off` | `since`, `last_event_type`, `last_sleep_duration_min`, `is_partial_boundary`, `memo` |

### 授乳・ミルク系センサー

| キー | 日本語名 | 単位 / 型 | 内容・主な属性 |
| --- | --- | --- | --- |
| `last_feeding` | 最後の授乳・ミルク | `timestamp` | 母乳・ミルク・搾母乳の最新時刻（`type`, `value_ml`, `last_side`, `left_time_sec`, `right_time_sec`, `total_time_sec`, `memo`） |
| `last_breastfeeding` | 最後の母乳 | `timestamp` | 母乳の最新時刻（`last_side`, `left_time_sec`, `right_time_sec`, `total_time_sec`, `value_ml`, `memo`） |
| `last_breastfeeding_side` | 最後の母乳の側 | `左` / `右` | 最後に授乳した側（次にどちらからあげるかの目安に便利） |
| `last_formula` | 最後のミルク | `timestamp` | ミルクの最新時刻（`value`, `unit`, `memo`） |
| `last_formula_amount` | 最後のミルク量 | `mL` | 最後のミルクの量（未入力時は `unknown`、0 扱いしない） |
| `last_expressed_breast_milk` | 最後の搾母乳 | `timestamp` | 搾母乳の最新時刻（`value`, `unit`, `memo`） |
| `last_pumping` | 最後の搾乳 | `timestamp` | 搾乳の最新時刻（`value`, `unit`, `memo`） |
| `today_formula_total` | 今日のミルク合計量 | `mL` | 当日のミルク合計量（`count`, `last_24h_total_ml`, `last_24h_count`, `range_covers_today`） |
| `today_breastfeeding_count` | 今日の母乳回数 | 回数 | 当日の母乳回数（`total_left_sec`, `total_right_sec`, `total_time_sec`, `total_ml`, `last_24h_*`） |
| `today_expressed_milk_total` | 今日の搾母乳合計量 | `mL` | 当日の搾母乳合計量（`count`, `last_24h_total_ml`, `last_24h_count`） |
| `today_pumping_total` | 今日の搾乳合計量 | `mL` | 当日の搾乳合計量（`count`, `last_24h_total_ml`, `last_24h_count`） |
| `today_feeding_count` | 今日の授乳合計回数 | 回数 | 母乳＋ミルク＋搾母乳の当日合計回数（`total_milk_ml`, `last_24h_count`） |

### 睡眠・排泄系センサー

| キー | 日本語名 | 単位 / 型 | 内容・主な属性 |
| --- | --- | --- | --- |
| `last_sleep` | 最後に寝た時刻 | `timestamp` | 最新の `Sleep` 時刻（`memo`） |
| `last_wakeup` | 最後に起きた時刻 | `timestamp` | 最新の `WakeUp` 時刻（`last_sleep_duration_min`, `memo`） |
| `today_sleep_duration` | 今日の睡眠時間 | `min` | 当日 0 時以降の合計睡眠時間（進行中の睡眠も含む。`sessions_count`, `last_sleep_duration_min`, `last_24h_sleep_min`, `is_partial_boundary`） |
| `last_pee` | 最後のおしっこ | `timestamp` | 最新の `Pee` 時刻（`memo`） |
| `today_pee_count` | 今日のおしっこ回数 | 回数 | 当日のおしっこ回数（`last_24h_count`） |
| `last_poop` | 最後のうんち | `timestamp` | 最新の `Poop` 時刻（`amount`, `hardness`, `color`, `memo`） |
| `today_poop_count` | 今日のうんち回数 | 回数 | 当日のうんち回数（`last_24h_count`） |

### 身体測定系センサー

| キー | 日本語名 | 単位 | 主な属性 |
| --- | --- | --- | --- |
| `latest_temperature` | 最新体温 | `°C` | `measured_at`, `memo` |
| `latest_weight` | 最新体重 | `kg` | `measured_at`, `memo` |
| `latest_height` | 最新身長 | `cm` | `measured_at`, `memo` |
| `latest_head_circumference` | 最新頭囲 | `cm` | `measured_at`, `memo` |
| `latest_chest_circumference` | 最新胸囲 | `cm` | `measured_at`, `memo` |
| `latest_foot_size_left` | 最新足サイズ（左） | `cm` | `measured_at`, `memo` |
| `latest_foot_size_right` | 最新足サイズ（右） | `cm` | `measured_at`, `memo` |

### 食事・生活・健康・メモ・総合センサー

| キー | 日本語名 | 型 | 主な属性 |
| --- | --- | --- | --- |
| `last_solid` / `last_meal` / `last_snack` | 最後の離乳食 / ごはん / おやつ | `timestamp` | `today_count`, `last_24h_count`, `memo` |
| `last_drink` | 最後ののみもの | `timestamp` | `value`, `unit`, `today_total_ml`, `last_24h_total_ml`, `today_count`, `memo` |
| `last_bath` / `last_walking` | 最後のお風呂 / さんぽ | `timestamp` | `memo` |
| `last_medicine` / `last_cough` / `last_vomiting` | 最後のくすり / せき / 吐く | `timestamp` | `today_count`, `last_24h_count`, `memo` |
| `last_rash` / `last_injury` / `last_hospital` / `last_vaccine` | 最後の発疹 / けが / 病院 / 予防接種 | `timestamp` | `memo` |
| `last_milestone` / `last_other` / `last_memo` | 最後のできた / その他 / メモ | `timestamp` | `memo` |
| `latest_record` | 最新の記録 | `timestamp` | 全種別を通じた最新記録（`type`, `memo`, `recent_records` [直近 20 件]） |
| `last_custom1`〜`last_custom10` | 最後のカスタム 1〜10 | `timestamp` | `today_count`, `last_24h_count`, `memo`（※デフォルト無効） |

> **カスタム 1〜10 の有効化方法**: デバイスのエンティティ一覧（設定 ＞ デバイスとサービス ＞ PiyoLog ＞ デバイス）で「無効なエンティティ」を開き、使用したい `last_custom*` を選択して **「有効にする」** をオンにしてください。

### 診断センサー

| キー | 日本語名 | 内容・主な属性 |
| --- | --- | --- |
| `feed_status` | フィード状態 | `正常` / `レート制限中` / `一時エラー` / `利用不可` / `データ量超過` / `スキーマ非対応`（`period`, `generated_at`, `range_from`, `range_to`, `last_success`, `last_error_code`, `consecutive_errors`, `current_interval_sec`） |
| `record_count` | 取得レコード数 | フィード内の総レコード件数（`counts_by_type`） |

> **当日集計と `range_covers_today` について**:
> ぴよログのデータフィードは「生成時刻から過去 24 時間〜28 日」の範囲を返します。`24h` フィードの場合も通常は当日 0 時以前をカバーしますが、取得時刻やキャッシュ状況により当日 0 時以降の全記録が含まれるかを `range_covers_today` 属性で確認できます。

---

## サービスアクション (`piyolog.get_records`)

Home Assistant のセンサー属性には DB 保存上限（16KB）があるため、全記録の生 JSON は DB へ保存せず、定期取得した最新スナップショットを **メモリ上に保持** しています。

オートメーションやテンプレートから「特定種別の過去記録をまとめて取得したい」場合は、`piyolog.get_records` アクションを呼び出すことで、**追加の API 通信を行わずに** 条件一致する記録一覧をレスポンスとして受け取れます。

### 呼び出し例

```yaml
action: piyolog.get_records
data:
  types:
    - Formula
    - BreastFeeding
    - Poop
  limit: 30
response_variable: piyolog_result
```

### パラメータ

| パラメータ | 必須 | 説明 |
| --- | --- | --- |
| `entry_id` | 任意 | 対象の ConfigEntry ID（複数フィード登録時のみ必須） |
| `types` | 任意 | 抽出したい記録種別の配列（例: `["Formula", "Temperature"]`）。省略時は全種別 |
| `limit` | 任意 | 返却する最大件数（新しい順、`1`〜`500`、デフォルト `50`） |

### レスポンス例

```json
{
  "entry_id": "01J...",
  "name": "赤ちゃん",
  "period": "24h",
  "status": "ok",
  "generated_at": "2026-09-09T03:00:00+00:00",
  "range": {
    "from": "2026-09-08T03:00:00+00:00",
    "to": "2026-09-09T03:00:00+00:00"
  },
  "count": 2,
  "total_in_snapshot": 18,
  "records": [
    {
      "event_id": "example-003",
      "datetime": "2026-09-09T01:00:00+00:00",
      "type": "Poop",
      "details": { "amount": "normal", "hardness": "soft", "color": "yellow" }
    },
    {
      "event_id": "example-001",
      "datetime": "2026-09-08T07:00:00+00:00",
      "type": "Formula",
      "value": 120.0,
      "unit": "ml",
      "memo": "完飲"
    }
  ]
}
```

---

## ダッシュボード・オートメーション活用例

### ダッシュボードカード例（Entities カード）

`device_class: timestamp` のセンサーは、Home Assistant 標準のカードで自動的に「○時間前」「○分前」と表示されます。

```yaml
type: entities
title: ぴよログ ステータス
entities:
  - entity: binary_sensor.piyolog_sleeping
  - entity: sensor.piyolog_last_feeding
  - entity: sensor.piyolog_last_breastfeeding_side
  - entity: sensor.piyolog_today_formula_total
  - entity: sensor.piyolog_today_sleep_duration
  - entity: sensor.piyolog_last_pee
  - entity: sensor.piyolog_last_poop
  - entity: sensor.piyolog_latest_temperature
```

※エンティティ ID のプレフィックス（`piyolog_`）は、セットアップ時に指定した表示名に応じて変わります。

### オートメーション例（フィード異常時の通知）

期限切れや失効でフィード取得ができなくなった際に通知する例です。

```yaml
alias: ぴよログ フィード異常通知
trigger:
  - trigger: state
    entity_id: sensor.piyolog_feed_status
    to:
      - unavailable
      - too_large
      - unsupported_schema
action:
  - action: notify.notify
    data:
      title: ぴよログ 連携エラー
      message: >
        フィード状態が {{ states('sensor.piyolog_feed_status') }} になりました。
        ぴよログアプリでデータフィードの状態を確認してください。
```

---

## トラブルシューティング

| 症状 / エラー | 原因と対処 |
| --- | --- |
| `HTTP 404` (`feed_unavailable`) | URL の誤り・有効期限切れ・失効・プレミアム終了による停止が考えられます。なお、**フィード作成直後は数秒〜1分ほど 404 になる場合がある**ため、少し待ってから再試行してください。URL を再発行した場合は「再構成」から差し替えてください。 |
| `HTTP 413` (`feed_response_too_large`) | 取得期間内のデータ量が上限を超えています。ぴよログアプリ側でより短い期間（例: `28d` → `7d` や `3d`）のフィードを作成し、「再構成」から切り替えてください。 |
| `HTTP 429` (`rate_limit_exceeded`) | アクセス頻度制限に達しています。本インテグレーションは自動的にバックオフ（間隔延長）して回復を試みます。複数フィード利用時は更新間隔を長めに設定してください。 |
| 記録を追加したのにすぐ反映されない | ぴよログ API 側のサーバーキャッシュ（`24h`: 1分 / `3d`: 2分 / `7d`: 5分 / `28d`: 15分）と、HA の更新間隔の組み合わせにより数分のラグが生じます。 |
| 体温や体重などが `unknown` になる | 選択したフィード期間内（例: 過去 24 時間）に該当の記録が存在しない場合は `unknown` となります。 |

---

## 開発・テスト

ローカル環境で単体テストを実行できます（Python 3.12+ / `uv`）。

```bash
uv run pytest -v
```

SSH 経由で開発中の Home Assistant へ手動デプロイする場合の補助スクリプトも同梱しています：

```bash
HA_SSH_TARGET="root@<homeassistant-host>" ./scripts/deploy.sh
```

---

## ライセンス

[MIT License](LICENSE)
