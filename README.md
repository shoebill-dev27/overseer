# Overseer

**Claude Code Remote Operations Console** — ローカルで走る複数の Claude Code（tmux）セッションを
リモートから「監視」し、入力待ちになったら承認操作（Y/N/Enter/中断）を送り込むためのツール。

外出先や別端末から、Tailscale / localhost 越しに「あのセッション、承認待ちで止まってないか？」を
確認し、必要なら一押しで先へ進められる状態を作ることが目的。

---

## これは何を解決するか

Claude Code を `tmux` 上で長時間走らせると、こちらの承認（`Do you want to proceed?` など）で
頻繁に停止する。端末に張り付いていないと進まない。Overseer は次を肩代わりする:

- 監視対象 tmux セッションを定期的に覗き、**RUNNING / WAITING_FOR_INPUT / FINISHED** を判定
- 画面内容（スナップショット）を**シークレットを伏字化した上で**Web UIに表示
- ブラウザから **Y / N / Enter / 中断(STOP)**（および任意テキスト送信）を、二段階確認つきで送信

---

## アーキテクチャ

3つの独立コンポーネント。Agent と Backend は **同一ホスト上の localhost** で通信する前提。

```
   ┌──────────────────────────────────────────────────────────────┐
   │  開発マシン（Claude Code が走っているホスト）                  │
   │                                                                │
   │   tmux: claude-foo ┐                                           │
   │   tmux: claude-bar ┤  capture-pane / send-keys                 │
   │                    ▼                                           │
   │            ┌──────────────┐   HMAC署名つき HTTP (/internal)    │
   │            │ Local Agent  │ ───────────────────────────┐       │
   │            │ (agent/)     │                            ▼       │
   │            └──────────────┘                   ┌──────────────┐ │
   │                                               │  FastAPI     │ │
   │                                               │  Backend     │ │
   │   ブラウザ ◀── WebSocket / REST ──────────────│  (backend/)  │ │
   │   (frontend/) GitHub OAuth ロール認可          │  + SQLite    │ │
   │                                               └──────────────┘ │
   └──────────────────────────────────────────────────────────────┘
```

### 1. Local Agent (`agent/`)
- `tmux` セッション（既定では `claude-` で始まる名前）を `poll_interval_seconds`（既定10秒）ごとに監視。
- `tmux capture-pane` で画面を取得 → `config/waiting_patterns.yaml` の正規表現で入力待ちを検知。
- 取得テキストは送信前に**スクラビング**（APIキー・トークン・パスワード等を `[REDACTED]` に置換）。
- 状態とスナップショットを Backend の Internal API へ push。`heartbeat` で生存通知。
- Backend にある **CONFIRMED** 操作を取得し、`config/agent.yaml` で許可されたものだけを
  `tmux send-keys` で実行（Agent 側が最終ゲート）。

### 2. Backend (`backend/`)
- FastAPI + SQLite（aiosqlite, WALモード）。フロントの静的配信も担う。
- **GitHub OAuth 2.0** ログイン + ユーザーID ホワイトリスト + ロール認可。
- セッション状態を保存し、変化を **WebSocket** で全クライアントへブロードキャスト。
- 操作の**二段階確認**（作成→確認）と監査ログ（INSERT専用）。

### 3. Frontend (`frontend/`)
- 素の HTML/CSS/JS（ビルド不要）。Backend が `/` から配信。
- 左にセッション一覧、右に詳細スナップショットと操作バー。WebSocket でリアルタイム更新。

---

## データフロー（状態とアクション）

**状態の流れ（監視）**
```
tmux画面 → capture-pane → パターン照合 → scrub → POST /internal/sessions/update
        → SQLite保存 → WebSocket broadcast → ブラウザ更新
```

**アクションの流れ（操作、二段階確認）**
```
ブラウザ: POST /api/sessions/{id}/actions        → status=PENDING_CONFIRM
ブラウザ: POST /api/actions/{id}/confirm (120秒以内) → status=CONFIRMED
Agent:   GET  /internal/actions/pending          → CONFIRMED を取得
Agent:   tmux send-keys 実行                      → POST /internal/actions/{id}/result
                                                  → status=EXECUTED または FAILED
```
確認されないまま 120 秒（`ACTION_TTL_SECONDS`）を過ぎた操作は **EXPIRED** に落ちる。
同一 `idempotency_key` の操作は重複作成されない。

---

## ロールと権限

ロールはランク制（上位は下位を包含）。初回ログイン時は **VIEWER** で作成され、昇格はDBを直接更新する。

| ロール    | できること                                        |
|-----------|---------------------------------------------------|
| VIEWER    | セッション一覧 / 詳細 / スナップショットの閲覧      |
| OPERATOR  | 上記 + アクション作成・確認（Y/N/Enter/STOP、任意テキスト）|
| ADMIN     | 上記すべて（現状 ADMIN 専用エンドポイントは無し）   |

---

## セットアップ

### 前提
- Python 3.10+
- `tmux`（Agent が監視・操作に使用）
- 監視したい Claude Code を `claude-<名前>` という名前の tmux セッションで起動しておく
  （プレフィックスは `config/agent.yaml` の `session_prefix` で変更可）
- GitHub OAuth App（ログイン用）

### 手順
```bash
# 1. 依存をインストール（backend / agent それぞれに venv を作る）
make setup

# 2. 環境変数を用意（テンプレからコピーして実値を記入）
cp backend/.env.example backend/.env
cp agent/.env.example   agent/.env
#  - SECRET_KEY と AGENT_HMAC_SECRET を生成して両方に設定
#    （AGENT_HMAC_SECRET は backend/agent で同一値にする）
#  - GitHub OAuth の各値、ALLOWED_GITHUB_USER_IDS を記入

# 3. DB 初期化（SQLite スキーマ作成）
make db-init
```

`SECRET_KEY` / `AGENT_HMAC_SECRET` の生成例:
```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

---

## 起動

```bash
make run-backend   # FastAPI + UI を 0.0.0.0:8000 で起動
make run-agent     # Local Agent を起動（別ターミナル）
# または
make dev           # backend と agent を同時起動
```

起動後、ブラウザで `http://127.0.0.1:8000` を開き「GitHub でログイン」。
起動時の self-check は **HEALTHY / DEGRADED / FAILED** を標準出力に表示する
（`SECRET_KEY` と `AGENT_HMAC_SECRET` が無いと FAILED で停止）。

---

## 設定ファイル

| ファイル                        | 役割                                                          |
|---------------------------------|---------------------------------------------------------------|
| `backend/.env`                  | Backend のシークレット・OAuth・ホワイトリスト（テンプレ: `.env.example`） |
| `agent/.env`                    | Agent の HMAC シークレット・接続先（テンプレ: `.env.example`） |
| `config/agent.yaml`             | 監視間隔・対象プレフィックス・許可アクション種別など           |
| `config/waiting_patterns.yaml`  | 入力待ち検知の正規表現。**Agent再起動なしで60秒ごとに動的リロード** |

> 補足: 接続先 URL は `agent/.env` の `BACKEND_URL` が使われる（`agent.yaml` の `backend.url` は
> ポーリング間隔等のメタ設定用）。

---

## API 概要

| メソッド | パス                                | 認可        | 用途                     |
|----------|-------------------------------------|-------------|--------------------------|
| GET      | `/auth/github`                      | —           | OAuth 開始               |
| GET      | `/auth/github/callback`             | —           | OAuth コールバック       |
| POST     | `/auth/logout`                      | VIEWER      | ログアウト               |
| GET      | `/auth/me`                          | VIEWER      | 自分の情報               |
| GET      | `/api/sessions`                     | VIEWER      | セッション一覧 + Agent状態 |
| GET      | `/api/sessions/{id}`                | VIEWER      | セッション詳細           |
| GET      | `/api/sessions/{id}/snapshot`       | VIEWER      | 画面スナップショット     |
| POST     | `/api/sessions/{id}/actions`        | OPERATOR    | アクション作成（要確認） |
| POST     | `/api/actions/{id}/confirm`         | OPERATOR    | アクション確認           |
| GET      | `/api/actions/{id}`                 | VIEWER      | アクション状態取得       |
| WS       | `/ws/sessions`                      | Cookie認証  | 状態のリアルタイム配信   |
| GET      | `/health`                           | —           | ヘルスチェック           |
| `/internal/*`                      | —          | HMAC署名    | Agent 専用（下記参照）   |

`/internal/*`（`sessions/update`・`heartbeat`・`actions/pending`・`actions/{id}/result`）は
`X-Agent-Timestamp` / `X-Agent-Signature` による HMAC-SHA256 認証（タイムスタンプ ±300秒）。

---

## セキュリティモデル

- **公開ネットワークに晒さない前提**。Tailscale や localhost からのアクセスを想定。
- CORS は `APP_BASE_URL` のみ許可。セキュリティヘッダ（CSP / X-Frame-Options 等）を付与。
- GitHub の**数値ユーザーID**でホワイトリスト認可（username は表示用のみ）。
- HTTP セッションはサーバ側保存のランダムトークン（24時間 TTL、revoke 可）。
- Agent ↔ Backend は HMAC 署名 + タイムスタンプで認証。さらに `/internal` はクライアントIPを
  **ループバック（127.0.0.1 / ::1）に限定**し、外部送信元は HMAC 以前に 403 で拒否する。
- スナップショット・ログは保存/送信前に**シークレット伏字化**（`scrubber.py`）。

### 既知の制約・注意点（要改善）
- `/internal` のループバック制限は送信元IPに依存するため、`/internal` を localhost へ転送する
  リバースプロキシを前段に置く場合は、プロキシ側で `/internal` を遮断すること
  （プロキシ経由だと送信元が 127.0.0.1 に見えるため）。
- `SECRET_KEY` は起動時の存在チェックのみで、現状コードでは署名に未使用（将来用に予約）。
- OAuth の state はプロセス内メモリ保持（単一プロセス前提。再起動でログイン途中状態は消える）。
- `SEND_TEXT`（任意テキスト送信）は実装済みだが、安全のため `config/agent.yaml` で
  **既定無効**。有効化するとブラウザから tmux へ任意文字列を送れるため、運用判断で明示的に
  `SEND_TEXT: true` にすること（OPERATOR 以上＋二段階確認は常に必須）。

---

## 開発

```bash
make lint    # ruff check + ruff format --check（backend / agent）
make test    # pytest（backend 41 + agent 19 = 60 件）
```

### プロジェクト構成
```
overseer/
├── backend/
│   ├── app/
│   │   ├── main.py          # FastAPI 起動・self-check・静的配信
│   │   ├── config.py        # 環境変数ロード
│   │   ├── database.py      # SQLite スキーマ・接続
│   │   ├── auth.py          # ロール認可の依存性
│   │   ├── sessions.py      # HTTPセッション（Cookie）管理
│   │   ├── audit.py         # 監査ログ
│   │   ├── scrubber.py      # シークレット伏字化
│   │   ├── ws_manager.py    # WebSocket 接続管理
│   │   └── routers/         # auth / sessions / actions / internal / ws
│   └── tests/
├── agent/
│   ├── agent.py             # 監視メインループ
│   ├── client.py            # HMAC署名つき Internal API クライアント
│   ├── tmux_monitor.py      # capture-pane / send-keys / list-sessions
│   ├── pattern_matcher.py   # 入力待ちパターン照合（動的リロード）
│   ├── scrubber.py          # シークレット伏字化
│   └── tests/
├── frontend/                # 素の HTML / CSS / JS
├── config/                  # agent.yaml / waiting_patterns.yaml
└── Makefile
```

---

## ステータス

- **Phase 1**（閲覧 Web UI）— 完了
- **Phase 2**（操作アクション: SEND_Y/N/ENTER/STOP、二段階確認・HMAC・監査ログ）— 完了
- **Phase 3**（`SEND_TEXT`: 任意テキスト送信。既定無効）— 完了

`make lint` 緑 / `make test` 全 60 件 pass。
