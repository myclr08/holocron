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
  fillMail(settings);
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


// --- E-posta (Outlook) --------------------------------------------------
//
// Kart yalnizca Windows'ta is gorur; baska isletim sisteminde dugmeler pasif
// kalir ve uyari gorunur. Klasor agaci Outlook'tan cekilir, secim JSON liste
// olarak `mail.folders` ayarinda durur.

const mailState = { selected: ["Gelen Kutusu"], tree: null, supported: false };

function parseFolders(value) {
  if (Array.isArray(value)) return value.slice();
  const text = String(value || "").trim();
  if (!text) return [];
  if (text.startsWith("[")) {
    try {
      const parsed = JSON.parse(text);
      return Array.isArray(parsed) ? parsed.map((item) => String(item)) : [];
    } catch (err) {
      return [];
    }
  }
  return text
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
}

// Uc adres listesi ayri alanlarda durur; her biri yalnizca kendi basligina bakar.
const MAIL_ADDRESS_FIELDS = [
  ["mail.from_addresses", "mail-from"],
  ["mail.to_addresses", "mail-to"],
  ["mail.cc_addresses", "mail-cc"],
];

function fillMail(settings) {
  mailState.supported = settings.mail_supported !== false;
  mailState.selected = parseFolders(settings["mail.folders"]);
  if (!mailState.selected.length) mailState.selected = ["Gelen Kutusu"];

  document.getElementById("mail-enabled").checked = settings["mail.enabled"] === "1";
  MAIL_ADDRESS_FIELDS.forEach(([key, id]) => {
    document.getElementById(id).value = settings[key] || "";
  });
  document.getElementById("mail-days").value = settings["mail.days"] || "30";
  document.getElementById("mail-body-limit").value = settings["mail.body_limit"] || "4000";
  document.getElementById("mail-scan-on-refresh").checked =
    settings["mail.scan_on_refresh"] !== "0";

  document.getElementById("mail-unsupported").hidden = mailState.supported;
  ["mail-folders-fetch", "mail-test", "mail-scan"].forEach((id) => {
    document.getElementById(id).disabled = !mailState.supported;
  });
  renderFolders();
}

function collectMail() {
  const payload = {
    "mail.enabled": document.getElementById("mail-enabled").checked ? "1" : "0",
    "mail.folders": JSON.stringify(mailState.selected),
    "mail.days": document.getElementById("mail-days").value.trim() || "30",
    "mail.body_limit": document.getElementById("mail-body-limit").value.trim() || "4000",
    "mail.scan_on_refresh": document.getElementById("mail-scan-on-refresh").checked ? "1" : "0",
  };
  MAIL_ADDRESS_FIELDS.forEach(([key, id]) => {
    payload[key] = document.getElementById(id).value.trim();
  });
  return payload;
}

/** Secili klasorler; agac cekilmediyse yalnizca secim listesi cizilir. */
function renderFolders() {
  const box = document.getElementById("mail-folders");
  box.textContent = "";
  const hint = document.getElementById("mail-folders-hint");

  if (!mailState.tree) {
    mailState.selected.forEach((path) => box.appendChild(folderRow(path, path, 0, true)));
    hint.textContent = mailState.supported
      ? "Başka klasör eklemek için Klasörleri getir."
      : "Bu özellik yalnız Windows'ta Outlook ile çalışır.";
    return;
  }

  const draw = (nodes, depth) => {
    nodes.forEach((node) => {
      // "Arama Klasörleri" başlığı gerçek bir klasör değil: seçilemez.
      const label = node.selectable === false ? node.name : `${node.name} (${node.count})`;
      box.appendChild(folderRow(node.path, label, depth, false, node.selectable !== false));
      if (node.children && node.children.length) draw(node.children, depth + 1);
    });
  };
  draw(mailState.tree, 0);
  hint.textContent = `${mailState.selected.length} klasör seçili.`;
}

function folderRow(path, label, depth, standalone, selectable = true) {
  const row = document.createElement("label");
  row.className = "checkbox folder-row" + (selectable ? "" : " is-virtual");
  row.style.paddingLeft = depth * 14 + "px";

  const box = document.createElement("input");
  box.type = "checkbox";
  box.checked = selectable && mailState.selected.includes(path);
  box.disabled = !selectable || (!mailState.supported && !standalone);
  box.addEventListener("change", () => {
    const next = mailState.selected.filter((item) => item !== path);
    if (box.checked) next.push(path);
    mailState.selected = next;
    const hint = document.getElementById("mail-folders-hint");
    if (mailState.tree) hint.textContent = `${mailState.selected.length} klasör seçili.`;
  });

  const text = document.createElement("span");
  text.textContent = label;
  row.appendChild(box);
  row.appendChild(text);
  return row;
}

async function saveMail(quiet) {
  const status = document.getElementById("mail-status");
  const data = await api("/api/settings", {
    method: "PUT",
    body: JSON.stringify(collectMail()),
  });
  fillMail(data.settings || {});
  if (!quiet) setStatus(status, "E-posta ayarları kaydedildi.", "ok");
  return data.settings || {};
}

async function fetchFolders() {
  const status = document.getElementById("mail-status");
  setStatus(status, "Klasörler okunuyor...", null);
  try {
    await saveMail(true);
    const data = await api("/api/mail/folders");
    mailState.tree = data.folders || [];
    renderFolders();
    setStatus(status, "Klasör ağacı geldi. İstediklerinizi işaretleyip kaydedin.", "ok");
  } catch (err) {
    setStatus(status, "Klasörler alınamadı: " + err.message, "error");
  }
}

async function testMail() {
  const status = document.getElementById("mail-status");
  setStatus(status, "Outlook deneniyor...", null);
  try {
    await saveMail(true);
    const data = await api("/api/mail/test", { method: "POST" });
    const result = data.result || {};
    const lines = [
      "Outlook'a bağlanıldı.",
      "Sürüm: " + (result.version || "-"),
      "Hesap: " + (result.account || "-"),
    ];
    (result.folders || []).slice(0, 8).forEach((folder) => {
      lines.push(`${folder.path}: ${folder.count} öğe`);
    });
    setStatus(status, lines.join("\n"), "ok");
  } catch (err) {
    setStatus(status, "Outlook'a bağlanılamadı: " + err.message, "error");
  }
}

function scanText(summary) {
  const parts = [
    `${summary.created || 0} görev oluşturuldu`,
    `${summary.appended || 0} mesaj mevcut göreve eklendi`,
    `${summary.scanned || 0} e-posta tarandı`,
  ];
  if (summary.skipped_calendar) parts.push(`${summary.skipped_calendar} takvim öğesi atlandı`);
  if (summary.skipped_seen) parts.push(`${summary.skipped_seen} mesaj zaten işlenmişti`);
  const lines = [parts.join(" · ")];
  (summary.errors || []).forEach((item) => lines.push(item.message || item.code));
  return lines.join("\n");
}

async function scanMail() {
  const status = document.getElementById("mail-status");
  setStatus(status, "E-postalar taranıyor...", null);
  try {
    await saveMail(true);
    const data = await api("/api/mail/scan", { method: "POST" });
    const summary = data.summary || {};
    setStatus(status, scanText(summary), (summary.errors || []).length ? "" : "ok");
  } catch (err) {
    setStatus(status, "Tarama yapılamadı: " + err.message, "error");
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
  document.getElementById("mail-save").addEventListener("click", () => {
    saveMail(false).catch((err) =>
      setStatus(document.getElementById("mail-status"), err.message, "error")
    );
  });
  document.getElementById("mail-folders-fetch").addEventListener("click", fetchFolders);
  document.getElementById("mail-test").addEventListener("click", testMail);
  document.getElementById("mail-scan").addEventListener("click", scanMail);
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
