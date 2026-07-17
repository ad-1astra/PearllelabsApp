// LeRobot Launch Lab -- frontend. Vanilla JS, no build step.
// Talks to the FastAPI backend in server.py over:
//   /ws/events        structured game-state events (XP, level-complete, ...)
//   /ws/term/{id}      raw PTY bytes for the currently running command
//   /api/*             REST actions that start a PTY session
//
// This page can be served two ways:
//  - locally, by the same uvicorn process that hosts the API (the `lerobot-launch-lab`
//    CLI) -- same-origin, no config needed.
//  - from a static host (e.g. Firebase Hosting) with the API running as a separate
//    "local helper" process on the user's own machine, since the PTY/USB/sudo access
//    this app needs only makes sense on the machine actually running the robot. In
//    that case we talk to 127.0.0.1 explicitly; override with ?host=127.0.0.1:PORT if
//    the local helper isn't on the default port.
const IS_LOCAL = location.hostname === "localhost" || location.hostname === "127.0.0.1";
const LOCAL_HELPER_HOST = new URLSearchParams(location.search).get("host") || "127.0.0.1:8090";
const API_ORIGIN = IS_LOCAL ? "" : `http://${LOCAL_HELPER_HOST}`;
const WS_ORIGIN = IS_LOCAL
  ? `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}`
  : `ws://${LOCAL_HELPER_HOST}`;

const MOTOR_LABELS = {
  shoulder_pan: "1 · Shoulder Pan",
  shoulder_lift: "2 · Shoulder Lift",
  elbow_flex: "3 · Elbow Flex",
  wrist_flex: "4 · Wrist Flex",
  wrist_roll: "5 · Wrist Roll",
  gripper: "6 · Gripper",
};

const state = {
  quest: null, // { levels, level_titles, steps }
  app: null, // snapshot from /ws/events
  currentLevel: null,
  currentSessionId: null,
  arm: { find_ports: "follower", set_motor_ids: "follower", calibrate: "follower" },
  installSteps: [],
};

let term = null;
let fitAddon = null;
let termSocket = null;
let eventsSocket = null;

// ---------------------------------------------------------------- icons
// Minimal inline SVG icons (stroke="currentColor", transparent background) --
// used sparingly for utility actions, not as decoration on every button.

const ICON_PATHS = {
  refresh: '<path d="M21 12a9 9 0 1 1-3-6.7"/><path d="M21 3v6h-6"/>',
  login: '<path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><path d="M15 3h6v6"/><path d="M10 14 21 3"/>',
  cube: '<path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/><path d="M3.27 6.96 12 12.01l8.73-5.05"/><path d="M12 22.08V12"/>',
  lock: '<rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/>',
  check: '<path d="M20 6 9 17l-5-5"/>',
};

function iconSvg(name, size) {
  const s = size || 16;
  return `<svg width="${s}" height="${s}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${ICON_PATHS[name] || ""}</svg>`;
}

function iconEl(name, size) {
  const span = document.createElement("span");
  span.className = "icon";
  span.innerHTML = iconSvg(name, size);
  return span;
}

// ---------------------------------------------------------------- utils

function $(sel, root) { return (root || document).querySelector(sel); }
function el(tag, attrs, children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (k === "class") node.className = v;
    else if (k === "text") node.textContent = v;
    else if (k.startsWith("on") && typeof v === "function") node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v);
  }
  for (const c of children || []) node.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
  return node;
}

async function api(path, opts) {
  const res = await fetch(API_ORIGIN + path, opts);
  return res.json();
}

async function runAction(action, params) {
  const res = await api("/api/run", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action, params: params || {} }),
  });
  if (res.error) {
    toast(res.error, "error");
    return null;
  }
  attachTerminal(res.session_id);
  return res.session_id;
}

function toast(message, kind) {
  const t = el("div", { class: `toast ${kind || ""}` }, [message]);
  $("#toast-root").appendChild(t);
  setTimeout(() => t.remove(), 5200);
}

// ---------------------------------------------------------------- terminal

function ensureTerminal() {
  if (term) return;
  term = new Terminal({
    convertEol: true,
    fontSize: 13,
    fontFamily: "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace",
    theme: {
      background: "#05070c",
      foreground: "#e7e9f2",
      cursor: "#f97316",
      selectionBackground: "#f9731644",
    },
  });
  fitAddon = new FitAddon.FitAddon();
  term.loadAddon(fitAddon);
  term.open($("#terminal"));
  fitAddon.fit();
  term.onData((data) => {
    if (termSocket && termSocket.readyState === WebSocket.OPEN) {
      termSocket.send(JSON.stringify({ type: "input", data }));
    }
  });
  new ResizeObserver(() => {
    if (!$("#terminal").offsetParent) return;
    fitAddon.fit();
    if (termSocket && termSocket.readyState === WebSocket.OPEN) {
      termSocket.send(JSON.stringify({ type: "resize", rows: term.rows, cols: term.cols }));
    }
  }).observe($("#terminal"));
}

function setTermDot(status) {
  const dot = $("#term-dot");
  dot.className = `term-dot ${status || ""}`;
}

function attachTerminal(sessionId) {
  ensureTerminal();
  if (termSocket) {
    try { termSocket.close(); } catch (e) { /* noop */ }
  }
  state.currentSessionId = sessionId;
  term.clear();
  setTermDot("running");
  $("#stop-btn").classList.remove("hidden");

  termSocket = new WebSocket(`${WS_ORIGIN}/ws/term/${sessionId}`);
  termSocket.onmessage = (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.type === "output") term.write(msg.data);
    else if (msg.type === "exit") {
      setTermDot(msg.code === 0 ? "success" : "error");
      $("#stop-btn").classList.add("hidden");
    }
  };
  termSocket.onopen = () => {
    fitAddon.fit();
    termSocket.send(JSON.stringify({ type: "resize", rows: term.rows, cols: term.cols }));
  };
}

async function stopCurrentSession() {
  if (!state.currentSessionId) return;
  await api(`/api/session/${state.currentSessionId}/stop`, { method: "POST" });
}

// ---------------------------------------------------------------- quest map

function levelStatus(levelKey) {
  const levels = state.quest.levels;
  const done = state.app.progress.levels_done;
  let prevDone = true;
  let status = "locked";
  for (const key of levels) {
    if (done[key]) status = key === levelKey ? "done" : status;
    else if (prevDone) status = key === levelKey ? "active" : status;
    else status = key === levelKey ? "locked" : status;
    if (key === levelKey) break;
    prevDone = done[key];
  }
  return status;
}

function renderTopbar() {
  const p = state.app.progress;
  $("#xp-label").textContent = `${p.xp} XP`;
  $("#progress-label").textContent = `${p.done} / ${p.total} quests`;
  $("#xp-bar-fill").style.width = `${p.xp}%`;
  $("#rank-label").textContent = p.rank;
}

function renderQuestMap() {
  const path = $("#quest-path");
  path.innerHTML = "";
  const stepsByKey = {};
  for (const s of state.quest.steps) stepsByKey[s.key] = s;

  state.quest.levels.forEach((key, i) => {
    const step = stepsByKey[key];
    const status = levelStatus(key);
    const node = el("div", {
      class: `quest-node ${status}`,
      onclick: () => { if (status !== "locked") showLevel(key); },
    }, [
      el("div", { class: "node-badge" }, [status === "done" ? iconEl("check") : status === "locked" ? iconEl("lock") : String(i + 1)]),
      el("div", { class: "node-body" }, [
        el("div", { class: "node-title" }, [state.quest.level_titles[key]]),
        el("div", { class: "node-subtitle" }, [step ? step.subtitle : ""]),
      ]),
      el("div", { class: "node-reward" }, [step ? step.reward : ""]),
      el("div", { class: "node-status" }, [status === "done" ? "Done" : status === "locked" ? "Locked" : "Play"]),
    ]);
    path.appendChild(node);
  });
}

// ---------------------------------------------------------------- level panel builders

function field(label, inputEl) {
  return el("div", { class: "field" }, [el("label", { text: label }), inputEl]);
}

function textInput(id, value, placeholder) {
  return el("input", { type: "text", id, value: value || "", placeholder: placeholder || "" });
}

function numberInput(id, value) {
  return el("input", { type: "number", id, value: String(value) });
}

function armTabs(levelKey) {
  const ports = state.app.found_ports;
  const wrap = el("div", { class: "arm-tabs" });
  ["follower", "leader"].forEach((arm) => {
    const selected = state.arm[levelKey] === arm;
    const tab = el("div", {
      class: `arm-tab ${arm} ${selected ? "selected" : ""}`,
      onclick: () => { state.arm[levelKey] = arm; renderLevelPanel(); },
    }, [
      arm.toUpperCase(),
      el("span", { class: "port", text: ports[arm] || "no port yet" }),
    ]);
    wrap.appendChild(tab);
  });
  return wrap;
}

function customCommandWidget() {
  const input = textInput("custom-cmd", "", "Or type any command and press Enter...");
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && input.value.trim()) {
      runAction("custom", { cmd: input.value.trim() });
      input.value = "";
    }
  });
  return el("div", { class: "field" }, [el("label", { text: "Advanced" }), input]);
}

function renderInstallPanel(panel) {
  const list = el("div", { class: "install-list" });
  state.installSteps.forEach((s) => {
    list.appendChild(
      el("div", { class: `install-item ${s.already_installed ? "installed" : "pending"}`, "data-key": s.key }, [
        s.label,
        el("span", { class: "status-pill", text: s.already_installed ? "Ready" : "Pending" }),
      ])
    );
  });
  panel.appendChild(list);
  panel.appendChild(el("button", { class: "btn btn-primary btn-block", onclick: () => runAction("install_run") }, ["Run Install"]));
  panel.appendChild(el("button", { class: "btn btn-ghost btn-block", onclick: refreshInstallSteps }, [iconEl("refresh"), " Re-check"]));
  panel.appendChild(el("button", { class: "btn btn-ghost btn-block", onclick: () => runAction("hf_login") }, [iconEl("login"), " Hugging Face Login"]));
  panel.appendChild(el("div", { class: "hint-card" }, ["We check what's already installed and only fetch what's missing. Sudo prompts (git-lfs/ffmpeg) show up right in the terminal below -- type your password there."]));
}

function renderFindPortsPanel(panel) {
  panel.appendChild(armTabs("find_ports"));
  panel.appendChild(el("button", { class: "btn btn-primary btn-block", onclick: () => runAction("find_port", { arm: state.arm.find_ports }) }, ["Run Find Port"]));
  panel.appendChild(el("div", { class: "hint-card" }, ["Plug in one arm at a time. Tap it above, run Find Port, then unplug the USB cable when the terminal asks."]));
}

function renderSetMotorIdsPanel(panel) {
  panel.appendChild(armTabs("set_motor_ids"));
  const grid = el("div", { class: "motor-grid" });
  Object.entries(MOTOR_LABELS).forEach(([key, label]) => {
    const status = state.app.motor_status[key] || "pending";
    grid.appendChild(el("div", { class: `motor-chip ${status}`, "data-motor": key }, [el("span", { class: "dot" }), label]));
  });
  panel.appendChild(grid);
  panel.appendChild(el("button", { class: "btn btn-primary btn-block", onclick: () => runAction("setup_motors", { arm: state.arm.set_motor_ids }) }, ["Setup Motors"]));
  panel.appendChild(el("div", { class: "hint-card" }, ["Connect motors one at a time when prompted -- watch the checklist light up as each one is set."]));
}

function renderCalibratePanel(panel) {
  panel.appendChild(armTabs("calibrate"));
  panel.appendChild(el("button", { class: "btn btn-primary btn-block", onclick: () => runAction("calibrate", { arm: state.arm.calibrate }) }, ["Calibrate"]));
  panel.appendChild(el("div", { class: "hint-card" }, ["Center every joint, press Enter, then sweep each one through its full range."]));
}

function renderTeleoperatePanel(panel) {
  const camIndex = numberInput("teleop-cam", 0);
  panel.appendChild(field("Camera index", camIndex));
  panel.appendChild(el("button", { class: "btn btn-secondary btn-block", onclick: () => runAction("teleoperate", { cameras: [{ name: "front", index_or_path: Number(camIndex.value) }] }) }, ["Start Teleoperate"]));
  panel.appendChild(el("div", { class: "hint-card" }, ["Runs until you hit Stop. Move the leader arm and watch the follower mirror it."]));
}

function slug(text) { return (text || "task").toLowerCase().trim().replace(/[^a-z0-9]+/g, "_").replace(/^_|_$/g, "") || "task"; }

function renderRecordPanel(panel) {
  const user = state.app.hf_user || "your_hf_user";
  const task = textInput("record-task", "", "e.g. pick up the red block");
  const repo = textInput("record-repo", `${user}/task`);
  task.addEventListener("input", () => { repo.value = `${state.app.hf_user || "your_hf_user"}/${slug(task.value)}`; });
  panel.appendChild(field("Task description", task));
  panel.appendChild(field("Dataset repo id", repo));
  const eps = numberInput("record-eps", 50);
  const epLen = numberInput("record-eplen", 30);
  const resetLen = numberInput("record-reset", 10);
  panel.appendChild(el("div", { class: "field-row" }, [field("Episodes", eps), field("Episode s", epLen), field("Reset s", resetLen)]));
  const camIndex = numberInput("record-cam", 0);
  panel.appendChild(field("Camera index", camIndex));
  panel.appendChild(el("button", {
    class: "btn btn-primary btn-block",
    onclick: () => runAction("record", {
      repo_id: repo.value, task: task.value,
      num_episodes: Number(eps.value), episode_time_s: Number(epLen.value), reset_time_s: Number(resetLen.value),
      cameras: [{ name: "front", index_or_path: Number(camIndex.value) }],
    }),
  }, ["Start Recording"]));
  panel.appendChild(el("div", { class: "hint-card" }, ["Keys during recording: → next episode · ← redo · ESC finish & upload."]));
}

function renderTrainPanel(panel) {
  const repo = textInput("train-repo", state.app.last_dataset_repo_id || "");
  panel.appendChild(field("Dataset repo id", repo));
  const policy = el("select", { id: "train-policy" }, ["act", "diffusion", "smolvla", "pi0", "pi05", "xvla", "wall_x"].map((p) => el("option", { value: p, text: p })));
  const device = el("select", { id: "train-device" }, ["cuda", "mps", "cpu"].map((d) => el("option", { value: d, text: d })));
  panel.appendChild(el("div", { class: "field-row" }, [field("Policy", policy), field("Device", device)]));
  const batch = numberInput("train-batch", 8);
  const steps = numberInput("train-steps", 30000);
  panel.appendChild(el("div", { class: "field-row" }, [field("Batch size", batch), field("Steps", steps)]));
  const policyRepo = textInput("train-policy-repo", "");
  panel.appendChild(field("Push trained policy to (optional)", policyRepo));
  const wandb = el("input", { type: "checkbox", id: "train-wandb" });
  panel.appendChild(el("div", { class: "field" }, [el("label", {}, [wandb, " Log to Weights & Biases"])]));
  panel.appendChild(el("button", {
    class: "btn btn-primary btn-block",
    onclick: () => runAction("train", {
      repo_id: repo.value, policy_type: policy.value, device: device.value,
      batch_size: Number(batch.value), steps: steps.value ? Number(steps.value) : null,
      wandb_enable: wandb.checked, policy_repo_id: policyRepo.value || null,
    }),
  }, ["Start Training"]));
  panel.appendChild(el("div", { class: "hint-card" }, ["ACT + 5-10 epochs is the fastest first result. Watch for the loss plateauing, then stop and evaluate."]));
}

function renderEvaluatePanel(panel) {
  const mode = { value: "real" };
  const tabs = el("div", { class: "arm-tabs" });
  const real = el("div", { class: "arm-tab follower selected", text: "Real robot" });
  const sim = el("div", { class: "arm-tab leader", text: "Sim benchmark" });
  tabs.appendChild(real); tabs.appendChild(sim);
  panel.appendChild(tabs);

  const body = el("div", { class: "field" });
  panel.appendChild(body);

  function renderReal() {
    body.innerHTML = "";
    const task = textInput("eval-task", "");
    const repo = textInput("eval-repo", `${state.app.hf_user || "your_hf_user"}/eval_task`);
    const eps = numberInput("eval-eps", 10);
    const policyPath = textInput("eval-policy", state.app.last_policy_repo_id || "");
    body.appendChild(field("Task description", task));
    body.appendChild(field("Eval dataset repo id", repo));
    body.appendChild(field("Episodes", eps));
    body.appendChild(field("Policy path / repo id", policyPath));
    body.appendChild(el("button", {
      class: "btn btn-primary btn-block",
      onclick: () => runAction("eval_record", { repo_id: repo.value, task: task.value, num_episodes: Number(eps.value), policy_path: policyPath.value, cameras: [{ name: "front", index_or_path: 0 }] }),
    }, ["Run Real-Robot Eval"]));
  }
  function renderSim() {
    body.innerHTML = "";
    const policyPath = textInput("eval-sim-policy", state.app.last_policy_repo_id || "");
    const envType = textInput("eval-sim-env", "pusht");
    const nEps = numberInput("eval-sim-neps", 50);
    const batch = numberInput("eval-sim-batch", 10);
    const device = el("select", {}, ["cuda", "mps", "cpu"].map((d) => el("option", { value: d, text: d })));
    body.appendChild(field("Policy path / repo id", policyPath));
    body.appendChild(field("Env type", envType));
    body.appendChild(el("div", { class: "field-row" }, [field("Episodes", nEps), field("Batch size", batch)]));
    body.appendChild(field("Device", device));
    body.appendChild(el("button", {
      class: "btn btn-primary btn-block",
      onclick: () => runAction("eval_sim", { policy_path: policyPath.value, env_type: envType.value, n_episodes: Number(nEps.value), batch_size: Number(batch.value), device: device.value }),
    }, ["Run Sim Benchmark"]));
  }
  real.onclick = () => { real.classList.add("selected"); sim.classList.remove("selected"); renderReal(); };
  sim.onclick = () => { sim.classList.add("selected"); real.classList.remove("selected"); renderSim(); };
  renderReal();
}

const LEVEL_PANEL_RENDERERS = {
  install: renderInstallPanel,
  find_ports: renderFindPortsPanel,
  set_motor_ids: renderSetMotorIdsPanel,
  calibrate: renderCalibratePanel,
  teleoperate: renderTeleoperatePanel,
  record: renderRecordPanel,
  train: renderTrainPanel,
  evaluate: renderEvaluatePanel,
};

async function refreshInstallSteps() {
  const res = await api("/api/install/steps");
  state.installSteps = res.steps;
  if (state.currentLevel === "install") renderLevelPanel();
}

function renderLevelPanel() {
  const panel = $("#level-panel");
  panel.innerHTML = "";
  const renderer = LEVEL_PANEL_RENDERERS[state.currentLevel];
  if (renderer) renderer(panel);
  panel.appendChild(customCommandWidget());
}

async function showLevel(levelKey) {
  state.currentLevel = levelKey;
  const step = state.quest.steps.find((s) => s.key === levelKey);
  $("#level-kicker").textContent = `LEVEL ${state.quest.levels.indexOf(levelKey) + 1}`;
  $("#level-title").textContent = state.quest.level_titles[levelKey];
  $("#level-subtitle").textContent = step ? step.subtitle : "";
  $("#view-map").classList.add("hidden");
  $("#view-level").classList.remove("hidden");
  $("#stop-btn").classList.add("hidden");
  setTermDot("");
  if (levelKey === "install") await refreshInstallSteps();
  renderLevelPanel();
  ensureTerminal();
  setTimeout(() => fitAddon && fitAddon.fit(), 50);
}

function showMap() {
  state.currentLevel = null;
  $("#view-level").classList.add("hidden");
  $("#view-map").classList.remove("hidden");
  renderQuestMap();
}

// ---------------------------------------------------------------- confetti

function fireConfetti() {
  const canvas = $("#confetti-canvas");
  canvas.width = window.innerWidth;
  canvas.height = window.innerHeight;
  const ctx = canvas.getContext("2d");
  const colors = ["#f97316", "#3b82f6", "#34d399", "#fbbf24"];
  const pieces = Array.from({ length: 90 }, () => ({
    x: Math.random() * canvas.width,
    y: -20 - Math.random() * 100,
    vx: (Math.random() - 0.5) * 3,
    vy: 2 + Math.random() * 3,
    size: 5 + Math.random() * 5,
    color: colors[Math.floor(Math.random() * colors.length)],
    rot: Math.random() * Math.PI,
    vr: (Math.random() - 0.5) * 0.3,
  }));
  let frame = 0;
  function tick() {
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    frame += 1;
    let alive = false;
    for (const p of pieces) {
      p.x += p.vx; p.y += p.vy; p.rot += p.vr;
      if (p.y < canvas.height + 20) alive = true;
      ctx.save();
      ctx.translate(p.x, p.y);
      ctx.rotate(p.rot);
      ctx.fillStyle = p.color;
      ctx.fillRect(-p.size / 2, -p.size / 2, p.size, p.size * 0.6);
      ctx.restore();
    }
    if (alive && frame < 220) requestAnimationFrame(tick);
    else ctx.clearRect(0, 0, canvas.width, canvas.height);
  }
  tick();
}

// ---------------------------------------------------------------- events websocket

function connectEvents() {
  eventsSocket = new WebSocket(`${WS_ORIGIN}/ws/events`);
  eventsSocket.onopen = () => $("#conn-dot").className = "conn-dot online";
  eventsSocket.onclose = () => {
    $("#conn-dot").className = "conn-dot offline";
    setTimeout(connectEvents, 1500);
  };
  eventsSocket.onmessage = (ev) => handleEvent(JSON.parse(ev.data));
}

function handleEvent(msg) {
  switch (msg.type) {
    case "snapshot":
      state.app = msg;
      renderTopbar();
      if (!state.currentLevel) renderQuestMap();
      break;
    case "level_complete":
      state.app.progress = msg.progress;
      renderTopbar();
      if (!state.currentLevel) renderQuestMap();
      toast(`${state.quest.level_titles[msg.level] || msg.level} complete`, "level-up");
      fireConfetti();
      break;
    case "port_found":
      state.app.found_ports[msg.arm] = msg.port;
      if (state.currentLevel) renderLevelPanel();
      toast(`${msg.arm.toUpperCase()} port found: ${msg.port}`);
      break;
    case "motor_reset":
      state.app.motor_status = msg.motor_status;
      if (state.currentLevel === "set_motor_ids") renderLevelPanel();
      break;
    case "motor_status": {
      state.app.motor_status[msg.motor] = msg.status;
      const chip = document.querySelector(`.motor-chip[data-motor="${msg.motor}"]`);
      if (chip) chip.className = `motor-chip ${msg.status}`;
      break;
    }
    case "install_step": {
      const item = document.querySelector(`.install-item[data-key="${msg.key}"]`);
      if (item) {
        item.className = `install-item ${msg.status}`;
        item.querySelector(".status-pill").textContent = msg.status.replace("_", " ");
      }
      break;
    }
    case "hf_user":
      state.app.hf_user = msg.user;
      break;
    case "last_dataset":
      state.app.last_dataset_repo_id = msg.repo_id;
      break;
    case "last_policy":
      state.app.last_policy_repo_id = msg.repo_id;
      break;
    case "error_tip":
      toast(`Tip: ${msg.message}`, "error");
      break;
    case "session_exit":
      if (msg.session_id === state.currentSessionId) {
        setTermDot(msg.code === 0 ? "success" : "error");
        $("#stop-btn").classList.add("hidden");
      }
      break;
  }
}

// ---------------------------------------------------------------- init

function hydrateStaticIcons() {
  document.querySelectorAll("[data-icon]").forEach((el) => {
    el.innerHTML = iconSvg(el.getAttribute("data-icon"));
  });
}

function showLauncher() {
  // Served from a hosted origin (e.g. Firebase) instead of by the local helper itself.
  // A fetch()/WebSocket from here to 127.0.0.1 is exactly what browsers' Private
  // Network Access policy silently blocks (no prompt, no console-visible error to a
  // non-technical user -- it just looks like an empty page). A plain top-level
  // navigation via a real link click is NOT subject to that restriction, so send the
  // user there instead of trying to drive the app cross-origin.
  hydrateStaticIcons();
  $("#view-launcher").classList.remove("hidden");
  $("#view-map").classList.add("hidden");
  $("#launcher-link").href = `http://${LOCAL_HELPER_HOST}/`;
}

async function init() {
  if (!IS_LOCAL) {
    showLauncher();
    return;
  }
  hydrateStaticIcons();
  state.quest = await api("/api/quest");
  const snap = await api("/api/state");
  state.app = snap;
  renderTopbar();
  renderQuestMap();
  connectEvents();

  $("#back-btn").addEventListener("click", showMap);
  $("#stop-btn").addEventListener("click", stopCurrentSession);

  api("/api/hf_user").then((r) => { if (r.user) { state.app.hf_user = r.user; if (state.currentLevel) renderLevelPanel(); } });
}

init();
