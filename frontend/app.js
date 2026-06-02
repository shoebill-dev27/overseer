"use strict";

// Overseer frontend — fetch for data, WebSocket for real-time updates.
// Served from the same origin as the backend, so cookie auth is sent automatically.

// ── State ──────────────────────────────────────────────────────────────────

let selectedId = null;   // session id currently shown in detail
let sessionsCache = [];  // most recent session list
let currentRole = null;  // logged-in user's role (used to decide whether actions are allowed)
let ws = null;
let wsReconnectTimer = null;

// Only OPERATOR / ADMIN can perform actions
const CAN_OPERATE = () => currentRole === "OPERATOR" || currentRole === "ADMIN";

// Action labels (for the confirm dialog and button display)
const ACTION_LABELS = {
  SEND_Y: "Yes (y)",
  SEND_N: "No (n)",
  SEND_ENTER: "Enter",
  STOP: "STOP (Esc)",
  SEND_TEXT: "Send text",
};

// Max text length for SEND_TEXT (must match MAX_TEXT_LENGTH in the backend)
const MAX_TEXT_LENGTH = 1000;

// ── DOM helpers ────────────────────────────────────────────────────────────

const $ = (id) => document.getElementById(id);

function show(el) { el.classList.remove("hidden"); }
function hide(el) { el.classList.add("hidden"); }

// ── API ───────────────────────────────────────────────────────────────────

async function api(path, options = {}) {
  const resp = await fetch(path, { credentials: "same-origin", ...options });
  return resp;
}

// ── Authentication ──────────────────────────────────────────────────────────────────

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

// ── Session list ────────────────────────────────────────────────────────

async function refreshSessions() {
  const resp = await api("/api/sessions");
  if (resp.status !== 200) return;

  const data = await resp.json();
  sessionsCache = data.sessions;
  renderAgentBadge(data.agent);
  renderSessionList(data.sessions);

  // If the selected session still exists, refresh its detail too
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
    badge.textContent = "Agent: not connected";
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
    case "RUNNING": return "Running";
    case "WAITING_FOR_INPUT": return "Waiting";
    case "ERROR": return "Error";
    case "FINISHED": return "Finished";
    default: return status;
  }
}

// ── Session detail ────────────────────────────────────────────────────────

async function selectSession(id) {
  selectedId = id;
  setActionStatus(""); // clear the result message from the previous session
  renderSessionList(sessionsCache); // reflect the selected highlight

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
    ? `Category: ${session.waiting_category}`
    : "";
  $("detail-updated").textContent = session.last_updated_at
    ? `Updated: ${formatTime(session.last_updated_at)}`
    : "";

  renderActions(session);
}

// ── Actions ────────────────────────────────────────────────────────────

function renderActions(session) {
  const bar = $("action-bar");
  bar.innerHTML = "";

  // Show action buttons to OPERATOR+ only for sessions waiting for input
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

  // Arbitrary text send (SEND_TEXT): text input + send button
  const input = document.createElement("input");
  input.type = "text";
  input.className = "text-input";
  input.placeholder = "Text to send";
  input.maxLength = MAX_TEXT_LENGTH;

  const sendBtn = document.createElement("button");
  sendBtn.className = "btn-action";
  sendBtn.textContent = ACTION_LABELS.SEND_TEXT;
  sendBtn.addEventListener("click", () => {
    const text = input.value;
    if (text.trim() === "") {
      setActionStatus("Please enter some text.");
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
  const target = `Send to ${session.tmux_name}?`;
  const message = actionType === "SEND_TEXT"
    ? `Send "${textPayload}" — ${target}`
    : `Send "${label}" — ${target}`;
  if (!confirm(message)) return;

  // Idempotency key: generate a unique value per click to prevent double-send
  const idempotencyKey =
    `${session.id}-${actionType}-${Date.now()}-${Math.random().toString(36).slice(2)}`;

  const body = { action_type: actionType, idempotency_key: idempotencyKey };
  if (textPayload !== null) body.text_payload = textPayload;

  // 1) Create (PENDING_CONFIRM)
  let resp = await api(`/api/sessions/${session.id}/actions`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (resp.status !== 200) {
    setActionStatus(`Failed to create the action (${resp.status})`);
    return;
  }
  const action = await resp.json();

  // 2) Confirm (CONFIRMED) — only now can the Agent execute it
  resp = await api(`/api/actions/${action.id}/confirm`, { method: "POST" });
  if (resp.status !== 200) {
    setActionStatus(`Failed to confirm the action (${resp.status})`);
    return;
  }

  setActionStatus(`Sent "${label}". Waiting for execution...`);
  pollAction(action.id);
}

async function pollAction(actionId, attempt = 0) {
  if (attempt >= 15) {
    setActionStatus("Timed out waiting for the execution result.");
    return;
  }
  const resp = await api(`/api/actions/${actionId}`);
  if (resp.status !== 200) return;

  const action = await resp.json();
  if (action.status === "EXECUTED") {
    setActionStatus("Executed ✓");
  } else if (action.status === "FAILED") {
    setActionStatus(`Execution failed: ${action.failure_reason || "unknown"}`);
  } else if (action.status === "EXPIRED") {
    setActionStatus("The action expired.");
  } else {
    // Still CONFIRMED — wait for the Agent to execute and poll again
    setTimeout(() => pollAction(actionId, attempt + 1), 1000);
  }
}

function setActionStatus(text) {
  $("action-status").textContent = text;
}

async function loadSnapshot(id) {
  const resp = await api(`/api/sessions/${id}/snapshot`);
  if (resp.status !== 200) {
    $("snapshot").textContent = "(failed to fetch snapshot)";
    return;
  }
  const snap = await resp.json();
  $("snapshot").textContent = snap.content || "(no snapshot)";
  $("snapshot-meta").textContent = snap.captured_at
    ? `${snap.line_count} lines / captured: ${formatTime(snap.captured_at)}`
      + (snap.truncated ? " (truncated)" : "")
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
      // Re-fetch the list (robust and simple). Also refresh the selected session's snapshot.
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

// ── Startup ──────────────────────────────────────────────────────────────────

init();
