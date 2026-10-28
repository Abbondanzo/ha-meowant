// Live tab: IM/MQTT connection, work state and operations.
// Loaded after index.html's inline script and uses its helpers ($, el, api, log, jsonView, kv, badge, serial).

const OP_NAMES = { 1: "clean", 3: "level", 7: "rise", 8: "drop" };
const OP_STATE_NAMES = { 1: "start", 2: "pause", 3: "resume" };

let imLastId = 0;
let imTimer = null;

function showImStatus(status) {
  const boxes = Object.keys(status.contacts || {}).length;
  $("im-status").replaceChildren(
    badge(status.connected, "Connected", "Not connected"),
    status.connected ? ` · ${boxes} box${boxes === 1 ? "" : "es"} in contacts` : "");
  if (status.connected && serial && !(serial in status.contacts)) {
    $("im-status").append(el("span", { class: "warn" }, " · selected box isn't a contact"));
  }
  clearInterval(imTimer);
  imTimer = status.connected ? setInterval(pollImMessages, 2000) : null;
}

function showWorkState(state) {
  $("im-state-view").replaceChildren(
    kv([
      ["Status", el("strong", {}, fmt(state.status))],
      ["Work mode", `${state.work_mode} (${fmt(state.work_mode_name)})`],
      ["Work state", `${state.work_state} (${fmt(state.work_state_name)})`],
      ["Cause", fmt(state.cause)],
    ]),
    rawDetails("All fields", state.fields));
}

function messageLabel(m) {
  if (m.receipt) return `Receipt: ${m.receipt.status}${m.receipt.cause ? ` (${m.receipt.cause})` : ""}`;
  const from = m.serial || m.from_uid;
  if (m.state) return `${m.risp?.cmd === "0x400" ? "State push" : "State reply"} from ${from}: ${fmt(m.state.status)}`;
  if (m.risp?.cmd === "0x3fb" && m.risp.attr !== 1) return `Operation ack from ${from} (seq ${m.risp.seq})`;
  if (m.risp) return `RISP ${m.risp.cmd} from ${from}`;
  return m.error ? `Undecoded: ${m.error}` : `Message from ${m.from_uid ?? "?"}`;
}

function addImMessages(messages) {
  if (!messages.length) return;
  const box = $("im-messages");
  if (box.querySelector("p.muted")) box.replaceChildren();
  for (const m of messages) {
    imLastId = Math.max(imLastId, m.id);
    box.prepend(el("details", { class: "log-entry" },
      el("summary", {}, el("span", { class: m.error || m.receipt?.status === "receipt/unreachable" ? "err" : "ok" }, "● "),
        el("span", { class: "muted" }, new Date(m.at_ms).toLocaleTimeString() + "  "), messageLabel(m)),
      jsonView(m)));
    if (m.state && m.serial === serial) showWorkState(m.state);
  }
}

async function pollImMessages() {
  try {
    const data = await api(`/api/im/messages?after=${imLastId}`);
    addImMessages(data.messages);
    if (!data.connected) showImStatus(data);
  } catch {}
}

async function imCall(label, path, body) {
  let result;
  try {
    result = await api(path, { method: "POST", body });
  } catch (err) {
    result = { ok: false, error: String(err) };
  }
  log(label, result, result.ok);
  if (result.messages) addImMessages(result.messages);
  return result;
}

$("im-connect").addEventListener("click", async () => {
  $("im-connect").disabled = true;
  const result = await imCall("IM connect", "/api/im/connect");
  showImStatus(result.ok ? result : { connected: false });
  if (!result.ok) $("im-status").append(el("span", { class: "err" }, ` · ${result.error}`));
  $("im-connect").disabled = false;
});

$("im-disconnect").addEventListener("click", async () => {
  showImStatus(await imCall("IM disconnect", "/api/im/disconnect"));
});

$("im-state").addEventListener("click", async () => {
  if (!serial) return;
  const result = await imCall("Request state", `/api/devices/${encodeURIComponent(serial)}/im/state`);
  if (result.state) showWorkState(result.state);
  else $("im-state-view").replaceChildren(el("p", { class: "err small" }, result.error || "No reply"));
});

async function sendOperation(opMode, opState) {
  if (!serial) return;
  const name = `${OP_STATE_NAMES[opState] ?? opState} ${OP_NAMES[opMode] ?? `op ${opMode}`}`;
  if (!confirm(`Send "${name}" to ${serial}? This runs the box.`)) return;
  await imCall(`Operation: ${name}`, `/api/devices/${encodeURIComponent(serial)}/im/operation`,
    { op_mode: opMode, op_state: opState });
}

document.querySelectorAll("[data-op]").forEach((btn) => {
  btn.addEventListener("click", () => {
    const [mode, state] = btn.dataset.op.split(",").map(Number);
    sendOperation(mode, state);
  });
});

$("im-op-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const f = e.target.elements;
  sendOperation(Number(f.op_mode.value), Number(f.op_state.value));
});

$("im-clear").addEventListener("click", () => {
  $("im-messages").replaceChildren(el("p", { class: "muted small" }, "Nothing received yet."));
});

// The dev server may already hold a connection (survives page reloads).
api("/api/im/messages").then((data) => { showImStatus(data); addImMessages(data.messages); }).catch(() => {});
