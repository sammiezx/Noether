// Reactive Emmy HUD: arc-reactor-style orb on a Canvas, transcript panel,
// WebSocket events from the Python backend drive state + amplitude.

const STATE_COLORS = {
  starting:     { primary: "#888888", glow: "rgba(136,136,136,0.3)", label: "BOOTING" },
  booting:      { primary: "#888888", glow: "rgba(136,136,136,0.3)", label: "BOOTING" },
  idle:         { primary: "#4ed1ff", glow: "rgba(78,209,255,0.2)",  label: "STANDBY" },
  listening:    { primary: "#00d4ff", glow: "rgba(0,212,255,0.45)",  label: "LISTENING" },
  transcribing: { primary: "#7fffd4", glow: "rgba(127,255,212,0.4)", label: "TRANSCRIBING" },
  thinking:     { primary: "#ff9a3c", glow: "rgba(255,154,60,0.5)",  label: "THINKING" },
  speaking:     { primary: "#00ffff", glow: "rgba(0,255,255,0.6)",   label: "SPEAKING" },
  paused:       { primary: "#ff9a3c", glow: "rgba(255,154,60,0.25)", label: "PAUSED" },
  terminating:  { primary: "#ff4d6d", glow: "rgba(255,77,109,0.7)",  label: "TERMINATING" },
  error:        { primary: "#ff4d6d", glow: "rgba(255,77,109,0.6)",  label: "ERROR" },
};

const orb = document.getElementById("orb");
const ctx = orb.getContext("2d");
const statusEl = document.getElementById("status");
const transcriptEl = document.getElementById("transcript");
const pauseBtn = document.getElementById("pause-btn");
const pauseLabel = document.getElementById("pause-label");
const muteLine = document.getElementById("mute-line");
const brandEl = document.getElementById("brand");
const subbrandEl = document.getElementById("subbrand");

// Update the header brand to reflect the active persona ("emmy" = default).
function setPersona(key, label) {
  if (!brandEl || !subbrandEl) return;
  if (!key || key === "emmy") {
    brandEl.textContent = "E M M Y";
    subbrandEl.textContent = "The voice of Noether";
  } else {
    brandEl.textContent = (label || key).toUpperCase();
    subbrandEl.textContent = "Emmy · in character";
  }
}

let currentState = "starting";
let amplitude = 0;
let smoothedAmplitude = 0;
let isPaused = false;
let t0 = performance.now();
let ws = null;

function setState(s) {
  currentState = s in STATE_COLORS ? s : "idle";
  const spec = STATE_COLORS[currentState];
  statusEl.textContent = `— ${spec.label} —`;
  statusEl.style.color = spec.primary;
}

function isAtBottom() {
  // Within 30px of the bottom counts as "at bottom" — gives the user some
  // play for partial scroll.
  return transcriptEl.scrollHeight - transcriptEl.scrollTop - transcriptEl.clientHeight < 30;
}

function pushTranscript(role, text) {
  const stick = isAtBottom();
  const li = document.createElement("li");
  li.className = role;
  li.textContent = text;
  transcriptEl.appendChild(li);
  if (stick) transcriptEl.scrollTop = transcriptEl.scrollHeight;
  // Cap log lines to keep DOM light.
  while (transcriptEl.children.length > 200) {
    transcriptEl.removeChild(transcriptEl.firstChild);
  }
}

// Map tool_use_id -> the <li> element rendering it, so tool_result can update in place.
const toolElements = new Map();

function summarizeArgs(input) {
  if (!input || typeof input !== "object") return "";
  const parts = [];
  for (const [k, v] of Object.entries(input)) {
    let val;
    if (typeof v === "string") {
      val = v.length > 60 ? v.slice(0, 60) + "…" : v;
      val = `"${val}"`;
    } else if (Array.isArray(v)) {
      val = `[${v.join(",")}]`;
    } else {
      val = String(v);
    }
    parts.push(`${k}=${val}`);
  }
  const joined = parts.join(", ");
  return joined.length > 100 ? joined.slice(0, 100) + "…" : joined;
}

function pushToolUse(id, name, input) {
  const stick = isAtBottom();
  const li = document.createElement("li");
  li.className = "tool running";
  li.innerHTML = `
    <span class="tool-status">RUNNING</span>
    <span class="tool-name">⚡ ${name}</span>
    <span class="tool-args">${summarizeArgs(input)}</span>
    <span class="tool-result"></span>
  `;
  transcriptEl.appendChild(li);
  if (stick) transcriptEl.scrollTop = transcriptEl.scrollHeight;
  toolElements.set(id, li);
  while (transcriptEl.children.length > 200) {
    transcriptEl.removeChild(transcriptEl.firstChild);
  }
}

function updateToolResult(id, summary, isError) {
  const li = toolElements.get(id);
  if (!li) return;
  li.classList.remove("running");
  li.classList.add(isError ? "error" : "done");
  li.querySelector(".tool-status").textContent = isError ? "ERROR" : "DONE";
  li.querySelector(".tool-result").textContent = summary;
  toolElements.delete(id);
}

// ---------- Canvas rendering ----------

function resize() {
  const dpr = window.devicePixelRatio || 1;
  const rect = orb.getBoundingClientRect();
  orb.width = rect.width * dpr;
  orb.height = rect.height * dpr;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
}
window.addEventListener("resize", resize);
resize();

function draw(now) {
  const t = (now - t0) / 1000;
  const w = orb.clientWidth;
  const h = orb.clientHeight;
  const cx = w / 2;
  const cy = h / 2;
  const baseRadius = Math.min(w, h) * 0.35;
  const spec = STATE_COLORS[currentState];

  // Smooth amplitude toward target (frames at ~60Hz, target at ~33Hz)
  smoothedAmplitude += (amplitude - smoothedAmplitude) * 0.2;
  // Decay amplitude when no fresh frame comes in.
  amplitude *= 0.92;

  ctx.clearRect(0, 0, w, h);

  // Background glow halo modulated by amplitude.
  const haloR = baseRadius * (1.6 + smoothedAmplitude * 0.6);
  const halo = ctx.createRadialGradient(cx, cy, baseRadius * 0.5, cx, cy, haloR);
  halo.addColorStop(0, spec.glow);
  halo.addColorStop(1, "rgba(0,0,0,0)");
  ctx.fillStyle = halo;
  ctx.beginPath();
  ctx.arc(cx, cy, haloR, 0, Math.PI * 2);
  ctx.fill();

  // State-driven dynamics
  let outerRot, innerRot, pulse;
  switch (currentState) {
    case "listening":
      outerRot = t * 0.25;
      innerRot = -t * 0.6;
      pulse = 1 + smoothedAmplitude * 0.25;
      break;
    case "transcribing":
      outerRot = t * 0.8;
      innerRot = -t * 1.0;
      pulse = 1 + Math.sin(t * 6) * 0.04;
      break;
    case "thinking":
      outerRot = t * 1.6;
      innerRot = -t * 2.2;
      pulse = 1 + Math.sin(t * 4) * 0.06;
      break;
    case "speaking":
      outerRot = t * 0.5;
      innerRot = -t * 0.9;
      pulse = 1 + Math.abs(Math.sin(t * 8)) * 0.12;
      break;
    case "error":
      outerRot = 0;
      innerRot = 0;
      pulse = 1 + Math.sin(t * 12) * 0.08;
      break;
    default:
      outerRot = t * 0.15;
      innerRot = -t * 0.25;
      pulse = 1 + Math.sin(t * 1.5) * 0.02;
  }

  // Outermost dotted ring (24 segments)
  drawDottedRing(cx, cy, baseRadius * 1.4 * pulse, 24, outerRot, spec.primary, 0.45);

  // Tick marks ring (60 ticks)
  drawTickRing(cx, cy, baseRadius * 1.2 * pulse, baseRadius * 1.27 * pulse, 60, outerRot * -0.5, spec.primary, 0.4);

  // Segmented arc ring
  drawSegmentedRing(cx, cy, baseRadius * 1.05 * pulse, 8, innerRot, spec.primary, 0.7, 0.35);

  // Solid inner ring (with gap)
  ctx.strokeStyle = spec.primary;
  ctx.lineWidth = 2;
  ctx.globalAlpha = 0.9;
  ctx.beginPath();
  const gap = 0.15;
  ctx.arc(cx, cy, baseRadius * 0.9 * pulse, gap, Math.PI * 2 - gap);
  ctx.stroke();
  ctx.globalAlpha = 1;

  // Core orb — radial gradient + inner highlight
  const coreR = baseRadius * 0.55 * pulse;
  const core = ctx.createRadialGradient(cx, cy - coreR * 0.3, 0, cx, cy, coreR);
  core.addColorStop(0, "#ffffff");
  core.addColorStop(0.3, spec.primary);
  core.addColorStop(1, "rgba(0,0,0,0.6)");
  ctx.fillStyle = core;
  ctx.beginPath();
  ctx.arc(cx, cy, coreR, 0, Math.PI * 2);
  ctx.fill();

  // Crosshair lines through the orb
  ctx.strokeStyle = spec.primary;
  ctx.globalAlpha = 0.25;
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(cx - baseRadius * 1.5, cy);
  ctx.lineTo(cx - baseRadius * 1.1, cy);
  ctx.moveTo(cx + baseRadius * 1.1, cy);
  ctx.lineTo(cx + baseRadius * 1.5, cy);
  ctx.stroke();
  ctx.globalAlpha = 1;

  requestAnimationFrame(draw);
}

function drawDottedRing(cx, cy, r, count, rotation, color, alpha) {
  ctx.fillStyle = color;
  ctx.globalAlpha = alpha;
  for (let i = 0; i < count; i++) {
    const a = (i / count) * Math.PI * 2 + rotation;
    const x = cx + Math.cos(a) * r;
    const y = cy + Math.sin(a) * r;
    ctx.beginPath();
    ctx.arc(x, y, 2.5, 0, Math.PI * 2);
    ctx.fill();
  }
  ctx.globalAlpha = 1;
}

function drawTickRing(cx, cy, rIn, rOut, count, rotation, color, alpha) {
  ctx.strokeStyle = color;
  ctx.globalAlpha = alpha;
  ctx.lineWidth = 1;
  for (let i = 0; i < count; i++) {
    const a = (i / count) * Math.PI * 2 + rotation;
    const x1 = cx + Math.cos(a) * rIn;
    const y1 = cy + Math.sin(a) * rIn;
    const x2 = cx + Math.cos(a) * rOut;
    const y2 = cy + Math.sin(a) * rOut;
    ctx.beginPath();
    ctx.moveTo(x1, y1);
    ctx.lineTo(x2, y2);
    ctx.stroke();
  }
  ctx.globalAlpha = 1;
}

function drawSegmentedRing(cx, cy, r, segments, rotation, color, fillRatio, alpha) {
  ctx.strokeStyle = color;
  ctx.lineWidth = 4;
  ctx.globalAlpha = alpha;
  const segAngle = (Math.PI * 2) / segments;
  for (let i = 0; i < segments; i++) {
    const a0 = i * segAngle + rotation;
    const a1 = a0 + segAngle * fillRatio;
    ctx.beginPath();
    ctx.arc(cx, cy, r, a0, a1);
    ctx.stroke();
  }
  ctx.globalAlpha = 1;
}

requestAnimationFrame(draw);

// ---------- Pause controls ----------

function setPaused(paused) {
  isPaused = paused;
  pauseBtn.classList.toggle("paused", paused);
  pauseLabel.textContent = paused ? "MIC PAUSED" : "MIC ACTIVE";
  muteLine.style.display = paused ? "" : "none";
}

function sendCommand(command) {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ command }));
  }
}

pauseBtn.addEventListener("click", () => sendCommand("toggle_pause"));

window.addEventListener("keydown", (e) => {
  if (e.code === "Space" && e.target === document.body) {
    e.preventDefault();
    sendCommand("toggle_pause");
  }
});

// ---------- Terminate (hold to confirm) ----------

const terminateBtn = document.getElementById("terminate-btn");
const terminateRingFill = terminateBtn.querySelector(".terminate-ring-fill");
const terminateLabel = terminateBtn.querySelector(".terminate-label");
const RING_CIRCUMFERENCE = 138.23; // 2 * π * 22 (matches stroke-dasharray)
const HOLD_MS = 1200;

let holdStart = 0;
let holdRaf = 0;
let terminated = false;

function setRingProgress(p) {
  // p in [0,1]: 0 = empty (full dashoffset), 1 = full ring (no offset)
  const clamped = Math.max(0, Math.min(1, p));
  terminateRingFill.style.strokeDashoffset = String(RING_CIRCUMFERENCE * (1 - clamped));
}

function tickHold() {
  if (terminated) return;
  const elapsed = performance.now() - holdStart;
  const progress = elapsed / HOLD_MS;
  setRingProgress(progress);
  if (progress >= 1) {
    terminated = true;
    terminateBtn.classList.add("armed");
    terminateBtn.classList.remove("holding");
    terminateLabel.textContent = "TERMINATING...";
    sendCommand("shutdown");
    return;
  }
  holdRaf = requestAnimationFrame(tickHold);
}

function startHold() {
  if (terminated) return;
  holdStart = performance.now();
  terminateBtn.classList.add("holding");
  holdRaf = requestAnimationFrame(tickHold);
}

function cancelHold() {
  if (terminated) return;
  cancelAnimationFrame(holdRaf);
  terminateBtn.classList.remove("holding");
  setRingProgress(0);
}

terminateBtn.addEventListener("mousedown", startHold);
terminateBtn.addEventListener("touchstart", startHold, { passive: true });
terminateBtn.addEventListener("mouseup", cancelHold);
terminateBtn.addEventListener("mouseleave", cancelHold);
terminateBtn.addEventListener("touchend", cancelHold);
terminateBtn.addEventListener("touchcancel", cancelHold);

// ---------- WebSocket ----------

function connect() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  ws = new WebSocket(`${proto}://${location.host}/ws`);
  ws.onmessage = (e) => {
    const msg = JSON.parse(e.data);
    switch (msg.type) {
      case "state":
        setState(msg.value);
        break;
      case "amplitude":
        amplitude = msg.value;
        break;
      case "user":
        pushTranscript("user", msg.text);
        break;
      case "assistant":
        pushTranscript("assistant", msg.text);
        break;
      case "log":
        pushTranscript("log", msg.text);
        break;
      case "paused":
        setPaused(msg.value);
        break;
      case "tool_use":
        pushToolUse(msg.id, msg.name, msg.input);
        break;
      case "tool_result":
        updateToolResult(msg.id, msg.summary, msg.is_error);
        break;
      case "spacetime":
        if (window.__spacetime) window.__spacetime.handle(msg);
        break;
      case "persona":
        setPersona(msg.key, msg.label);
        break;
    }
  };
  ws.onclose = () => {
    setState("error");
    statusEl.textContent = "— DISCONNECTED — retrying —";
    setTimeout(connect, 1500);
  };
}
connect();
