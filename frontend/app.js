"use strict";

// Overseer フロントエンド — fetch でデータ取得、WebSocket でリアルタイム更新。
// バックエンドと同一オリジンで配信されるため、Cookie 認証は自動で付与される。

// ── 状態 ──────────────────────────────────────────────────────────────────

let selectedId = null;   // 現在詳細表示中の session id
let sessionsCache = [];  // 直近のセッション一覧
let currentRole = null;  // ログインユーザーのロール（操作可否の判定に使用）
let ws = null;
let wsReconnectTimer = null;

// OPERATOR / ADMIN のみアクションを実行できる
const CAN_OPERATE = () => currentRole === "OPERATOR" || currentRole === "ADMIN";

// アクションのラベル（確認ダイアログ・ボタン表示用）
const ACTION_LABELS = {
  SEND_Y: "Yes (y)",
  SEND_N: "No (n)",
  SEND_ENTER: "Enter",
  STOP: "STOP (Esc)",
  SEND_TEXT: "テキスト送信",
};

// SEND_TEXT のテキスト最大長（backend の MAX_TEXT_LENGTH と一致させる）
const MAX_TEXT_LENGTH = 1000;

// ── DOM ヘルパ ────────────────────────────────────────────────────────────

const $ = (id) => document.getElementById(id);

function show(el) { el.classList.remove("hidden"); }
function hide(el) { el.classList.add("hidden"); }

// ── API ───────────────────────────────────────────────────────────────────

async function api(path, options = {}) {
  const resp = await fetch(path, { credentials: "same-origin", ...options });
  return resp;
}

// ── 認証 ──────────────────────────────────────────────────────────────────

async function init() {
  const resp = await api("/auth/me");
  if (resp.status === 200) {
    const user = await resp.json();
    startDashboard(user);
  } else {
    show($("login-view"));
  }
}

function startDashboard(user) {
  currentRole = user.role;
  $("user-label").textContent = `${user.github_login} (${user.role})`;
  $("logout-btn").addEventListener("click", logout);
  show($("dashboard-view"));

  refreshSessions();
  connectWebSocket();
}

async function logout() {
  await api("/auth/logout", { method: "POST" });
  location.reload();
}

// ── セッション一覧 ────────────────────────────────────────────────────────

async function refreshSessions() {
  const resp = await api("/api/sessions");
  if (resp.status !== 200) return;

  const data = await resp.json();
  sessionsCache = data.sessions;
  renderAgentBadge(data.agent);
  renderSessionList(data.sessions);

  // 選択中セッションが消えていなければ詳細も最新化
  if (selectedId !== null && sessionsCache.some((s) => s.id === selectedId)) {
    renderDetailHeader(sessionsCache.find((s) => s.id === selectedId));
  }
}

function renderAgentBadge(agent) {
  const badge = $("agent-badge");
  const status = agent ? agent.status : "NEVER_CONNECTED";
  if (status === "ONLINE") {
    badge.className = "badge badge-online";
    badge.textContent = "Agent: ONLINE";
  } else if (status === "OFFLINE") {
    badge.className = "badge badge-offline";
    badge.textContent = "Agent: OFFLINE";
  } else {
    badge.className = "badge badge-unknown";
    badge.textContent = "Agent: 未接続";
  }
}

function renderSessionList(sessions) {
  const list = $("session-list");
  list.innerHTML = "";

  if (sessions.length === 0) {
    show($("empty-hint"));
    return;
  }
  hide($("empty-hint"));

  for (const s of sessions) {
    const li = document.createElement("li");
    li.className = "session-item" + (s.id === selectedId ? " selected" : "");
    li.addEventListener("click", () => selectSession(s.id));

    const name = document.createElement("div");
    name.className = "name";
    name.textContent = s.tmux_name;

    const row = document.createElement("div");
    row.className = "row";

    const badge = document.createElement("span");
    badge.className = "badge badge-" + s.status;
    badge.textContent = statusLabel(s.status);

    const cat = document.createElement("span");
    cat.className = "hint";
    cat.textContent = s.waiting_category || "";

    row.appendChild(badge);
    row.appendChild(cat);
    li.appendChild(name);
    li.appendChild(row);
    list.appendChild(li);
  }
}

function statusLabel(status) {
  switch (status) {
    case "RUNNING": return "実行中";
    case "WAITING_FOR_INPUT": return "入力待ち";
    case "ERROR": return "エラー";
    case "FINISHED": return "終了";
    default: return status;
  }
}

// ── セッション詳細 ────────────────────────────────────────────────────────

async function selectSession(id) {
  selectedId = id;
  setActionStatus(""); // 前のセッションの実行結果メッセージをクリア
  renderSessionList(sessionsCache); // selected 強調を反映

  const session = sessionsCache.find((s) => s.id === id);
  if (session) renderDetailHeader(session);

  hide($("detail-empty"));
  show($("detail-content"));

  await loadSnapshot(id);
}

function renderDetailHeader(session) {
  $("detail-name").textContent = session.tmux_name;

  const badge = $("detail-status");
  badge.className = "badge badge-" + session.status;
  badge.textContent = statusLabel(session.status);

  $("detail-category").textContent = session.waiting_category
    ? `カテゴリ: ${session.waiting_category}`
    : "";
  $("detail-updated").textContent = session.last_updated_at
    ? `更新: ${formatTime(session.last_updated_at)}`
    : "";

  renderActions(session);
}

// ── アクション ────────────────────────────────────────────────────────────

function renderActions(session) {
  const bar = $("action-bar");
  bar.innerHTML = "";

  // 入力待ちのセッションに対してのみ、OPERATOR 以上に操作ボタンを表示
  if (!CAN_OPERATE() || session.status !== "WAITING_FOR_INPUT") {
    hide(bar);
    return;
  }

  for (const type of ["SEND_Y", "SEND_N", "SEND_ENTER", "STOP"]) {
    const btn = document.createElement("button");
    btn.className = "btn-action" + (type === "STOP" ? " btn-stop" : "");
    btn.textContent = ACTION_LABELS[type];
    btn.addEventListener("click", () => doAction(session, type));
    bar.appendChild(btn);
  }

  // 任意テキスト送信（SEND_TEXT）: 入力欄 + 送信ボタン
  const input = document.createElement("input");
  input.type = "text";
  input.className = "text-input";
  input.placeholder = "送信するテキスト";
  input.maxLength = MAX_TEXT_LENGTH;

  const sendBtn = document.createElement("button");
  sendBtn.className = "btn-action";
  sendBtn.textContent = ACTION_LABELS.SEND_TEXT;
  sendBtn.addEventListener("click", () => {
    const text = input.value;
    if (text.trim() === "") {
      setActionStatus("テキストを入力してください。");
      return;
    }
    doAction(session, "SEND_TEXT", text).then(() => { input.value = ""; });
  });

  bar.appendChild(input);
  bar.appendChild(sendBtn);
  show(bar);
}

async function doAction(session, actionType, textPayload = null) {
  const label = ACTION_LABELS[actionType];
  const target = `${session.tmux_name} に送信しますか?`;
  const message = actionType === "SEND_TEXT"
    ? `「${textPayload}」を ${target}`
    : `「${label}」を ${target}`;
  if (!confirm(message)) return;

  // 冪等キー: 二重送信を防ぐためクリックごとに一意な値を生成
  const idempotencyKey =
    `${session.id}-${actionType}-${Date.now()}-${Math.random().toString(36).slice(2)}`;

  const body = { action_type: actionType, idempotency_key: idempotencyKey };
  if (textPayload !== null) body.text_payload = textPayload;

  // 1) 作成（PENDING_CONFIRM）
  let resp = await api(`/api/sessions/${session.id}/actions`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (resp.status !== 200) {
    setActionStatus(`操作の作成に失敗しました (${resp.status})`);
    return;
  }
  const action = await resp.json();

  // 2) 確認（CONFIRMED）— ここで初めて Agent が実行可能になる
  resp = await api(`/api/actions/${action.id}/confirm`, { method: "POST" });
  if (resp.status !== 200) {
    setActionStatus(`操作の確認に失敗しました (${resp.status})`);
    return;
  }

  setActionStatus(`「${label}」を送信しました。実行待ち...`);
  pollAction(action.id);
}

async function pollAction(actionId, attempt = 0) {
  if (attempt >= 15) {
    setActionStatus("実行結果の確認がタイムアウトしました。");
    return;
  }
  const resp = await api(`/api/actions/${actionId}`);
  if (resp.status !== 200) return;

  const action = await resp.json();
  if (action.status === "EXECUTED") {
    setActionStatus("実行されました ✓");
  } else if (action.status === "FAILED") {
    setActionStatus(`実行に失敗しました: ${action.failure_reason || "不明"}`);
  } else if (action.status === "EXPIRED") {
    setActionStatus("操作が期限切れになりました。");
  } else {
    // CONFIRMED のまま — Agent の実行を待って再ポーリング
    setTimeout(() => pollAction(actionId, attempt + 1), 1000);
  }
}

function setActionStatus(text) {
  $("action-status").textContent = text;
}

async function loadSnapshot(id) {
  const resp = await api(`/api/sessions/${id}/snapshot`);
  if (resp.status !== 200) {
    $("snapshot").textContent = "(スナップショット取得失敗)";
    return;
  }
  const snap = await resp.json();
  $("snapshot").textContent = snap.content || "(スナップショットなし)";
  $("snapshot-meta").textContent = snap.captured_at
    ? `${snap.line_count} 行 / 取得: ${formatTime(snap.captured_at)}`
      + (snap.truncated ? " (切り詰めあり)" : "")
    : "";
}

function formatTime(iso) {
  const d = new Date(iso);
  return isNaN(d.getTime()) ? iso : d.toLocaleString();
}

// ── WebSocket ─────────────────────────────────────────────────────────────

function connectWebSocket() {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  ws = new WebSocket(`${proto}//${location.host}/ws/sessions`);

  ws.addEventListener("message", (ev) => {
    let msg;
    try { msg = JSON.parse(ev.data); } catch { return; }
    if (msg.type === "session_update") {
      // 一覧を再取得（堅牢・単純）。選択中セッションのスナップショットも更新。
      refreshSessions();
      if (msg.session_id === selectedId) loadSnapshot(selectedId);
    }
  });

  ws.addEventListener("close", scheduleReconnect);
  ws.addEventListener("error", () => ws.close());
}

function scheduleReconnect() {
  if (wsReconnectTimer) return;
  wsReconnectTimer = setTimeout(() => {
    wsReconnectTimer = null;
    connectWebSocket();
  }, 3000);
}

// ── 起動 ──────────────────────────────────────────────────────────────────

init();
