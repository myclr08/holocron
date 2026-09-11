// Ortak yardimcilar: API cagrisi, nabiz, kapatma, ikonlar, gorunum tercihleri.
const HEARTBEAT_INTERVAL_MS = 30000;

// Emoji yerine kendi cizdigimiz ince cizgi ikonlar (14px, currentColor).
const ICON_SHAPES = {
  clock: [
    ["circle", { cx: "8", cy: "8", r: "6.2" }],
    ["path", { d: "M8 4.4V8.2l2.5 1.5" }],
  ],
  pin: [
    ["path", { d: "M9.7 1.9 14.1 6.3l-2 .4-1 1a4.5 4.5 0 0 0-1.2 3.1l-.1 1.1-4.7-4.7 1.1-.1a4.5 4.5 0 0 0 3.1-1.2l1-1z" }],
    ["path", { d: "M5.1 10.9 1.9 14.1" }],
  ],
};

function icon(name) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("class", "icon");
  svg.setAttribute("viewBox", "0 0 16 16");
  svg.setAttribute("aria-hidden", "true");
  (ICON_SHAPES[name] || []).forEach(([tag, attrs]) => {
    const shape = document.createElementNS("http://www.w3.org/2000/svg", tag);
    Object.entries(attrs).forEach(([key, value]) => shape.setAttribute(key, value));
    svg.appendChild(shape);
  });
  return svg;
}

function reducedMotion() {
  return (
    typeof window.matchMedia === "function" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches
  );
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  let payload = null;
  try {
    payload = await response.json();
  } catch (err) {
    payload = null;
  }
  if (!response.ok) {
    const error = (payload && payload.error) || {};
    throw new Error(error.message || `Sunucu hatası (${response.status})`);
  }
  return payload;
}

function startHeartbeat() {
  const beat = () => {
    fetch("/api/heartbeat", { method: "POST" }).catch(() => {});
  };
  beat();
  setInterval(beat, HEARTBEAT_INTERVAL_MS);
}

async function shutdown() {
  if (!confirm("Holocron kapatılsın mı?")) return;
  try {
    await api("/api/shutdown", { method: "POST" });
  } catch (err) {
    // Sunucu cevap vermeden kapanabilir, bu beklenen durum.
  }
  document.body.innerHTML =
    '<div style="padding:40px;font-family:var(--font);color:var(--muted)">' +
    "Holocron kapatıldı. Bu sekmeyi kapatabilirsiniz.</div>";
}

/** Ayarlardan gelen gorunum tercihlerini sayfaya uygular. */
function applyAppearance(settings) {
  const prefs = {
    starfield: (settings || {})["ui.starfield"] !== "0",
    motion: (settings || {})["ui.motion"] !== "0",
  };
  document.documentElement.classList.toggle("no-motion", !prefs.motion);
  if (typeof Starfield !== "undefined") {
    Starfield.setMotion(prefs.motion);
    Starfield.setEnabled(prefs.starfield);
  }
  return prefs;
}

function saveSetting(key, value) {
  const payload = {};
  payload[key] = value;
  return api("/api/settings", { method: "PUT", body: JSON.stringify(payload) });
}

function bindShell() {
  const closeButton = document.querySelector("[data-action='shutdown']");
  if (closeButton) closeButton.addEventListener("click", shutdown);
  if (typeof Starfield !== "undefined") Starfield.mount(document.getElementById("starfield"));
  startHeartbeat();
}

function setStatus(element, message, kind) {
  element.textContent = message;
  element.className = "status" + (kind ? " " + kind : "");
  element.hidden = false;
}
