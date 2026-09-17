// Ortak yardimcilar: API cagrisi, nabiz, kapatma, ikonlar, gorunum tercihleri.
const HEARTBEAT_INTERVAL_MS = 30000;
// Sunucuya ulasilamiyorken daha sik denenir: geri geldiginde serit hemen kalksin.
const HEARTBEAT_RETRY_MS = 5000;
// Kac art arda basarisiz nabizdan sonra "sunucu gitti" deriz.
const HEARTBEAT_FAIL_LIMIT = 2;

// Emoji yerine kendi cizdigimiz ince cizgi ikonlar (14px, currentColor).
const ICON_SHAPES = {
  clock: [
    ["circle", { cx: "8", cy: "8", r: "6.2" }],
    ["path", { d: "M8 4.4V8.2l2.5 1.5" }],
  ],
  task: [
    ["rect", { x: "2.4", y: "2.4", width: "11.2", height: "11.2", rx: "2.4" }],
    ["path", { d: "M5.3 8.1 7.2 10l3.5-4" }],
  ],
  mail: [
    ["rect", { x: "1.8", y: "3.4", width: "12.4", height: "9.2", rx: "1.8" }],
    ["path", { d: "m2.6 4.8 5.4 3.9 5.4-3.9" }],
  ],
  chat: [
    ["rect", { x: "2.2", y: "2.6", width: "11.6", height: "8.4", rx: "2.2" }],
    ["path", { d: "M5.4 11v2.7L8.5 11" }],
  ],
  // Sefer paneli: seri alevi, rozet madalyasi, emir listesi, Jira kaydi.
  flame: [
    ["path", { d: "M8 1.6c2.6 2.2 3.9 4.1 3.9 6a3.9 3.9 0 0 1-7.8 0c0-1.2.5-2.3 1.6-3.4.2 1.3.7 2 1.5 2.2-.1-1.7.2-3.3 2.8-4.8z" }],
  ],
  medal: [
    ["circle", { cx: "8", cy: "10", r: "4.2" }],
    ["path", { d: "M5.4 6.3 3.3 1.9h9.4l-2.1 4.4" }],
  ],
  orders: [
    ["rect", { x: "2.6", y: "1.9", width: "10.8", height: "12.2", rx: "1.8" }],
    ["path", { d: "M5.2 5.6h5.6M5.2 8.2h5.6M5.2 10.8h3.4" }],
  ],
  issue: [
    ["rect", { x: "2.4", y: "2.4", width: "11.2", height: "11.2", rx: "2.4" }],
    ["path", { d: "M8 5.2v3.6M8 10.6v.1" }],
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

/** Metni panoya kopyalar; basarisiz olursa false doner (balon onu soyler).
 *
 * 127.0.0.1 guvenli baglam sayilir, bu yuzden `navigator.clipboard` normalde
 * calisir; eski tarayici ya da izin reddinde gizli bir textarea ile denenir.
 */
async function copyText(text) {
  const value = String(text || "");
  if (!value) return false;
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(value);
      return true;
    }
  } catch (err) {
    // Izin verilmedi; asagidaki yedek yol denenir.
  }
  try {
    const box = document.createElement("textarea");
    box.value = value;
    box.setAttribute("readonly", "");
    box.style.position = "fixed";
    box.style.top = "-1000px";
    document.body.appendChild(box);
    box.select();
    const ok = document.execCommand("copy");
    box.remove();
    return !!ok;
  } catch (err) {
    return false;
  }
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

// --- nabiz -------------------------------------------------------------
//
// Nabiz `setInterval` ile atiliyordu ve bu, dondurulan sekmelerde uygulamayi
// olduruyordu: Edge'in uyuyan sekmeleri, ekran kilidi ve arka plan sekme
// kisitlamasi zamanlayiciyi tamamen durduruyor, sunucu nabiz gelmedi diye
// kendini kapatiyordu. Uc degisiklik:
//
// 1. Zincirleme `setTimeout`: donmus sekme uyaninca birikmis cagrilar tek
//    seferde patlamaz, bir sonraki nabiz her zaman oncekinin bitisinden sayilir.
// 2. Sekme gorunur olunca / pencere odaklaninca / geri tusuyla donulunce
//    aninda nabiz: kullanici sayfaya dondugu anda sunucu bunu bilir.
// 3. Ust uste basarisiz nabizda serit: sunucu gercekten kapandiysa sayfa
//    sessiz kalmaz.

let heartbeatTimer = null;
let heartbeatInFlight = false;
let heartbeatStopped = false;
let heartbeatFailures = 0;
let serverGoneDismissed = false;

function scheduleHeartbeat(delayMs) {
  if (heartbeatTimer !== null) clearTimeout(heartbeatTimer);
  heartbeatTimer = setTimeout(() => {
    heartbeatTimer = null;
    beat();
  }, delayMs);
}

async function beat() {
  // Ayni anda iki istek olmasin: uyanan sekmede odak + gorunurluk birlikte gelir.
  if (heartbeatStopped || heartbeatInFlight) return;
  heartbeatInFlight = true;
  try {
    const response = await fetch("/api/heartbeat", { method: "POST", cache: "no-store" });
    if (!response.ok) throw new Error("heartbeat " + response.status);
    heartbeatFailures = 0;
    setServerGone(false);
  } catch (err) {
    heartbeatFailures += 1;
    if (heartbeatFailures >= HEARTBEAT_FAIL_LIMIT) setServerGone(true);
  } finally {
    heartbeatInFlight = false;
    if (!heartbeatStopped) {
      scheduleHeartbeat(heartbeatFailures ? HEARTBEAT_RETRY_MS : HEARTBEAT_INTERVAL_MS);
    }
  }
}

/** "Kapat" sonrasi nabiz susar: kapanmayi biz istedik, serit cikmasin. */
function stopHeartbeat() {
  heartbeatStopped = true;
  if (heartbeatTimer !== null) clearTimeout(heartbeatTimer);
  heartbeatTimer = null;
}

function startHeartbeat() {
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) beat();
  });
  window.addEventListener("focus", () => beat());
  // Geri/ileri onbelleginden donen sayfa: hic olay almadan canlanir.
  window.addEventListener("pageshow", () => beat());
  beat();
}

/** "Sunucu kapanmis" seridi: kalici ama kapatilabilir, sunucu donunce kalkar. */
function serverGoneBanner() {
  let bar = document.getElementById("server-gone");
  if (bar) return bar;
  bar = document.createElement("div");
  bar.id = "server-gone";
  bar.className = "server-gone";
  bar.setAttribute("role", "alert");
  bar.hidden = true;

  const text = document.createElement("span");
  text.className = "server-gone-text";
  text.textContent =
    "Holocron kapanmış görünüyor. holocron.bat ile yeniden başlatıp sayfayı yenileyin.";

  const close = document.createElement("button");
  close.type = "button";
  close.className = "server-gone-close";
  close.title = "Şeridi kapat";
  close.setAttribute("aria-label", "Şeridi kapat");
  close.textContent = "✕";
  close.addEventListener("click", () => {
    serverGoneDismissed = true;
    hideServerGone(bar);
  });

  bar.appendChild(text);
  bar.appendChild(close);
  document.body.insertBefore(bar, document.body.firstChild);
  return bar;
}

function hideServerGone(bar) {
  bar.hidden = true;
  document.documentElement.classList.remove("server-gone-open");
}

function setServerGone(gone) {
  if (!gone) {
    // Sunucu geri geldi: serit kalkar, kapatma tercihi de sifirlanir.
    serverGoneDismissed = false;
    const bar = document.getElementById("server-gone");
    if (bar) hideServerGone(bar);
    return;
  }
  if (serverGoneDismissed) return;
  const bar = serverGoneBanner();
  bar.hidden = false;
  document.documentElement.classList.add("server-gone-open");
}

async function shutdown() {
  if (!confirm("Holocron kapatılsın mı?")) return;
  stopHeartbeat();
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

// --- cekmece genisligi --------------------------------------------------
//
// Butun sag cekmeceler (kayit detayi, Teams aramalari) ayni genisligi
// paylasir: sol kenardaki tutamac surüklenir, olculen deger tarayicida
// saklanir. Depolama kapaliysa (gizli pencere, silinmis site verisi)
// okuma/yazma sessizce basarisiz olur ve varsayilan genislik kalir.

const DRAWER_WIDTH_KEY = "holocron.drawer.width";
const DRAWER_DEFAULT_WIDTH = 440;
const DRAWER_MIN_WIDTH = 360;
// Tutamac odakliyken ok tuslari bu kadar kaydirir.
const DRAWER_STEP = 20;

function drawerMaxWidth() {
  return Math.min(window.innerWidth * 0.9, 1400);
}

function clampDrawerWidth(px) {
  const max = Math.max(DRAWER_MIN_WIDTH, drawerMaxWidth());
  return Math.round(Math.min(Math.max(Number(px) || 0, DRAWER_MIN_WIDTH), max));
}

function readDrawerWidth() {
  try {
    const stored = Number(localStorage.getItem(DRAWER_WIDTH_KEY));
    if (stored > 0) return clampDrawerWidth(stored);
  } catch (err) {
    // Depolama kapali: varsayilan genislik kullanilir.
  }
  return 0;
}

function storeDrawerWidth(px) {
  try {
    localStorage.setItem(DRAWER_WIDTH_KEY, String(px));
  } catch (err) {
    // Yazilamadi; genislik yalnizca bu oturumda gecerli olur.
  }
}

function applyDrawerWidth(px) {
  document.documentElement.style.setProperty("--drawer-width", px + "px");
}

function setDrawerWidth(px, save) {
  const width = clampDrawerWidth(px);
  applyDrawerWidth(width);
  if (save) storeDrawerWidth(width);
  return width;
}

function currentDrawerWidth() {
  const drawer = document.querySelector(".drawer:not([hidden])");
  if (drawer) {
    const box = drawer.getBoundingClientRect();
    if (box.width) return box.width;
  }
  return readDrawerWidth() || DRAWER_DEFAULT_WIDTH;
}

/** Tek bir cekmeceye tutamak ekler (iki kez eklenmez). */
function addDrawerHandle(drawer) {
  if (!drawer || drawer.querySelector(".drawer-handle")) return;
  const handle = document.createElement("div");
  handle.className = "drawer-handle";
  handle.tabIndex = 0;
  handle.setAttribute("role", "separator");
  handle.setAttribute("aria-orientation", "vertical");
  handle.title = "Sürükleyerek genişlet · çift tık varsayılan · ok tuşları 20px";

  let startX = 0;
  let startWidth = 0;
  let dragging = false;

  handle.addEventListener("pointerdown", (event) => {
    dragging = true;
    startX = event.clientX;
    startWidth = currentDrawerWidth();
    try {
      handle.setPointerCapture(event.pointerId);
    } catch (err) {
      // Yakalama desteklenmiyorsa fare yine de izlenir.
    }
    // Surüklerken gecis animasyonu kapali: cekmece elin arkasindan gelmesin.
    document.documentElement.classList.add("drawer-resizing");
    event.preventDefault();
  });

  handle.addEventListener("pointermove", (event) => {
    if (!dragging) return;
    setDrawerWidth(startWidth + (startX - event.clientX), false);
  });

  const stop = (event) => {
    if (!dragging) return;
    dragging = false;
    try {
      handle.releasePointerCapture(event.pointerId);
    } catch (err) {
      // Yakalama zaten birakilmis olabilir.
    }
    document.documentElement.classList.remove("drawer-resizing");
    storeDrawerWidth(clampDrawerWidth(currentDrawerWidth()));
  };

  handle.addEventListener("pointerup", stop);
  handle.addEventListener("pointercancel", stop);
  handle.addEventListener("dblclick", () => setDrawerWidth(DRAWER_DEFAULT_WIDTH, true));
  handle.addEventListener("keydown", (event) => {
    if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
    event.preventDefault();
    const delta = event.key === "ArrowLeft" ? DRAWER_STEP : -DRAWER_STEP;
    setDrawerWidth(currentDrawerWidth() + delta, true);
  });

  drawer.insertBefore(handle, drawer.firstChild);
}

/** Saklanan genisligi uygular ve her cekmeceye tutamak takar. */
function bindDrawerResize() {
  const stored = readDrawerWidth();
  if (stored) applyDrawerWidth(stored);
  document.querySelectorAll(".drawer").forEach(addDrawerHandle);
}

function bindShell() {
  const closeButton = document.querySelector("[data-action='shutdown']");
  if (closeButton) closeButton.addEventListener("click", shutdown);
  if (typeof Starfield !== "undefined") Starfield.mount(document.getElementById("starfield"));
  bindDrawerResize();
  startHeartbeat();
}

function setStatus(element, message, kind) {
  element.textContent = message;
  element.className = "status" + (kind ? " " + kind : "");
  element.hidden = false;
}
