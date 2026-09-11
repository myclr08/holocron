// Ortak yardimcilar: API cagrisi, nabiz, kapatma.
const HEARTBEAT_INTERVAL_MS = 30000;

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
    '<div style="padding:40px;font-family:var(--hc-font);color:var(--hc-text-dim)">' +
    "Holocron kapatıldı. Bu sekmeyi kapatabilirsiniz.</div>";
}

function bindShell() {
  const closeButton = document.querySelector("[data-action='shutdown']");
  if (closeButton) closeButton.addEventListener("click", shutdown);
  startHeartbeat();
}

function setStatus(element, message, kind) {
  element.textContent = message;
  element.className = "status" + (kind ? " " + kind : "");
  element.hidden = false;
}
