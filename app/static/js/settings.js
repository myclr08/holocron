// Ayarlar ekrani: yukle, kaydet, baglantiyi sina, alan katalogunu cek.
const FIELD_IDS = {
  "jira.mode": "mode",
  "jira.base_url": "base-url",
  "jira.email": "email",
  "jira.username": "username",
  "jira.auth_type": "auth-type",
  "net.proxy_http": "proxy-http",
  "net.proxy_https": "proxy-https",
  "net.no_proxy": "no-proxy",
  "net.ca_file": "ca-file",
};

const STEP_MARK = { ok: "✓", fail: "✕", skip: "–" };

function proxyMode() {
  const picked = document.querySelector("input[name='proxy-mode']:checked");
  return picked ? picked.value : "system";
}

function setProxyMode(mode) {
  const value = ["system", "manual", "direct"].includes(mode) ? mode : "system";
  document.querySelectorAll("input[name='proxy-mode']").forEach((radio) => {
    radio.checked = radio.value === value;
  });
}

function applyModeVisibility() {
  const mode = document.getElementById("mode").value;
  const authType = document.getElementById("auth-type").value;
  const isCloud = mode === "cloud";
  document.getElementById("cloud-fields").hidden = !isCloud;
  document.getElementById("server-fields").hidden = isCloud;
  document.getElementById("server-username").hidden = isCloud || authType !== "basic";
  document.getElementById("secret-label").textContent = isCloud
    ? "API token"
    : authType === "basic"
      ? "Parola"
      : "Kişisel erişim anahtarı (PAT)";
}

function fillForm(settings) {
  Object.entries(FIELD_IDS).forEach(([key, id]) => {
    const el = document.getElementById(id);
    if (el) el.value = settings[key] || "";
  });
  document.getElementById("verify-ssl").checked = settings.verify_ssl !== false;
  document.getElementById("ipv4-first").checked = settings.ipv4_first !== false;
  setProxyMode(settings["net.proxy_mode"]);
  const badge = document.getElementById("secret-state");
  badge.textContent = settings.secret_set ? "ayarlı" : "ayarsız";
  badge.className = "badge" + (settings.secret_set ? " on" : "");
  document.getElementById("secret").placeholder = settings.secret_set
    ? "Değiştirmek için yeni değer yazın"
    : "Değer girin";
  applyModeVisibility();
}

function collectForm() {
  const payload = {};
  Object.entries(FIELD_IDS).forEach(([key, id]) => {
    const el = document.getElementById(id);
    if (el) payload[key] = el.value.trim();
  });
  payload["net.verify_ssl"] = document.getElementById("verify-ssl").checked;
  payload["net.ipv4_first"] = document.getElementById("ipv4-first").checked;
  payload["net.proxy_mode"] = proxyMode();
  const secret = document.getElementById("secret").value;
  if (secret) payload["jira.secret"] = secret;
  return payload;
}

function fillAppearance(settings) {
  const prefs = applyAppearance(settings);
  document.getElementById("ui-starfield").checked = prefs.starfield;
  document.getElementById("ui-motion").checked = prefs.motion;
}

async function loadSettings() {
  const data = await api("/api/settings");
  const settings = data.settings || {};
  fillForm(settings);
  fillAppearance(settings);
}

/** Gorunum kutulari aninda kaydedilir; Kaydet dugmesini beklemez. */
async function toggleAppearance(key, checked) {
  const status = document.getElementById("appearance-status");
  try {
    const data = await saveSetting(key, checked ? "1" : "0");
    fillAppearance(data.settings || {});
    setStatus(status, "Görünüm kaydedildi.", "ok");
  } catch (err) {
    setStatus(status, err.message, "error");
  }
}

async function replayCrawl() {
  const status = document.getElementById("appearance-status");
  try {
    await saveSetting("ui.crawl_seen", "");
    setStatus(
      status,
      "Açılış bir sonraki ana ekran ziyaretinde tekrar oynatılacak. " +
        "Hareketler kapalıyken açılış hiç gösterilmez.",
      "ok"
    );
  } catch (err) {
    setStatus(status, err.message, "error");
  }
}

async function saveSettings() {
  const status = document.getElementById("status");
  try {
    const data = await api("/api/settings", {
      method: "PUT",
      body: JSON.stringify(collectForm()),
    });
    document.getElementById("secret").value = "";
    fillForm(data.settings || {});
    setStatus(status, "Ayarlar kaydedildi.", "ok");
  } catch (err) {
    setStatus(status, err.message, "error");
  }
}

async function testConnection() {
  const status = document.getElementById("status");
  setStatus(status, "Bağlantı deneniyor...", null);
  try {
    await api("/api/settings", { method: "PUT", body: JSON.stringify(collectForm()) });
    document.getElementById("secret").value = "";
    const data = await api("/api/jira/test", { method: "POST" });
    const result = data.result || {};
    const lines = [
      "Bağlantı kuruldu.",
      "Kullanıcı: " + (result.display_name || "-"),
      "Sunucu: " + (result.server_title || "-"),
    ];
    if (result.version) lines.push("Sürüm: " + result.version);
    setStatus(status, lines.join("\n"), "ok");
    await loadSettings();
  } catch (err) {
    setStatus(status, "Bağlantı kurulamadı: " + err.message, "error");
  }
}

/** Teshis sonucunu adim listesi olarak cizer; kirmizi adimda oneri metni durur. */
function renderDiagnosis(result) {
  const box = document.getElementById("diagnosis");
  box.textContent = "";

  const head = document.createElement("p");
  head.className = "diagnosis-summary " + (result.ok ? "ok" : "error");
  head.textContent = result.summary || "";
  box.appendChild(head);

  const list = document.createElement("ol");
  list.className = "steps";
  (result.steps || []).forEach((step) => {
    const item = document.createElement("li");
    item.className = "step " + step.status;

    const mark = document.createElement("span");
    mark.className = "step-mark";
    mark.textContent = STEP_MARK[step.status] || "?";
    item.appendChild(mark);

    const body = document.createElement("div");
    body.className = "step-body";

    const title = document.createElement("div");
    title.className = "step-title";
    title.textContent = step.title;
    const ms = document.createElement("span");
    ms.className = "step-ms";
    ms.textContent = step.ms + " ms";
    title.appendChild(ms);
    body.appendChild(title);

    const message = document.createElement("div");
    message.className = "step-message";
    message.textContent = step.message || "";
    body.appendChild(message);

    item.appendChild(body);
    list.appendChild(item);
  });
  box.appendChild(list);

  (result.advice || []).forEach((line) => {
    const tip = document.createElement("p");
    tip.className = "diagnosis-advice";
    tip.textContent = line;
    box.appendChild(tip);
  });

  box.hidden = false;
}

async function diagnose() {
  const status = document.getElementById("status");
  const box = document.getElementById("diagnosis");
  box.hidden = true;
  setStatus(status, "Teşhis çalışıyor, her adım en çok beş saniye sürer...", null);
  try {
    await api("/api/settings", { method: "PUT", body: JSON.stringify(collectForm()) });
    document.getElementById("secret").value = "";
    const result = await api("/api/settings/diagnose", { method: "POST" });
    renderDiagnosis(result);
    setStatus(status, result.ok ? "Teşhis bitti: engel yok." : "Teşhis bitti.", result.ok ? "ok" : "error");
    await loadSettings();
  } catch (err) {
    setStatus(status, "Teşhis çalıştırılamadı: " + err.message, "error");
  }
}

async function refreshFields() {
  const status = document.getElementById("status");
  setStatus(status, "Alan kataloğu çekiliyor...", null);
  try {
    const data = await api("/api/jira/fields/refresh", { method: "POST" });
    setStatus(status, data.count + " alan kaydedildi.", "ok");
  } catch (err) {
    setStatus(status, "Alan kataloğu alınamadı: " + err.message, "error");
  }
}

document.addEventListener("DOMContentLoaded", () => {
  bindShell();
  document.getElementById("mode").addEventListener("change", applyModeVisibility);
  document.getElementById("auth-type").addEventListener("change", applyModeVisibility);
  document.getElementById("save").addEventListener("click", saveSettings);
  document.getElementById("test").addEventListener("click", testConnection);
  document.getElementById("diagnose").addEventListener("click", diagnose);
  document.getElementById("fields").addEventListener("click", refreshFields);
  document
    .getElementById("ui-starfield")
    .addEventListener("change", (event) => toggleAppearance("ui.starfield", event.target.checked));
  document
    .getElementById("ui-motion")
    .addEventListener("change", (event) => toggleAppearance("ui.motion", event.target.checked));
  document.getElementById("replay-crawl").addEventListener("click", replayCrawl);
  document.getElementById("clear-secret").addEventListener("click", async () => {
    const status = document.getElementById("status");
    if (!confirm("Kayıtlı sır silinsin mi?")) return;
    try {
      await api("/api/settings", { method: "PUT", body: JSON.stringify({ clear_secret: true }) });
      await loadSettings();
      setStatus(status, "Kayıtlı sır silindi.", "ok");
    } catch (err) {
      setStatus(status, err.message, "error");
    }
  });
  loadSettings().catch((err) => setStatus(document.getElementById("status"), err.message, "error"));
});
