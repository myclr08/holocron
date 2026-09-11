// Ayarlar ekrani: yukle, kaydet, baglantiyi sina, alan katalogunu cek.
const FIELD_IDS = {
  "jira.mode": "mode",
  "jira.base_url": "base-url",
  "jira.email": "email",
  "jira.username": "username",
  "jira.auth_type": "auth-type",
  "net.proxy_http": "proxy-http",
  "net.proxy_https": "proxy-https",
  "net.ca_file": "ca-file",
};

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
  const secret = document.getElementById("secret").value;
  if (secret) payload["jira.secret"] = secret;
  return payload;
}

async function loadSettings() {
  const data = await api("/api/settings");
  fillForm(data.settings || {});
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
  document.getElementById("fields").addEventListener("click", refreshFields);
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
