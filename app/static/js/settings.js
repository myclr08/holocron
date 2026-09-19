// Ayarlar ekrani: yukle, kaydet, baglantiyi sina, alan katalogunu cek.
//
// Sayfa GRUPLARA bolunmustur (soldaki dikey sekmeler). Her grup kendi
// bolumudur; ayni anda yalnizca biri gorunur. Secim `#grup` ile adreslenir
// ve localStorage'da hatirlanir, boylece kaydettikten sonra donen kullanici
// yine ayni yerde olur.

const GROUP_KEY = "holocron.settings.group";
const DEFAULT_GROUP = "jira";
// Ana ekranin son gorunumu (app.js'teki saveView() ile ayni anahtar).
const LAST_VIEW_KEY = "holocron.lastView";

/** Ana ekranin son gorunum hash'i; depolama kapaliysa bos doner. */
function lastMainViewHash() {
  try {
    return localStorage.getItem(LAST_VIEW_KEY) || "";
  } catch (err) {
    return "";
  }
}

/** Referrer ayni kokenden ve ana sayfaya (index) mi ait, onu soyler. */
function referrerIsMainPage(referrer, origin) {
  if (!referrer) return false;
  try {
    const url = new URL(referrer, origin);
    return url.origin === origin && (url.pathname === "/" || url.pathname === "/index.html");
  } catch (err) {
    return false;
  }
}

/** "Geri": ana sayfadan gelindiyse tarayici gecmisiyle doner, yoksa son gorunume gider. */
function goBackToMain() {
  if (referrerIsMainPage(document.referrer, window.location.origin)) {
    window.history.back();
    return;
  }
  window.location.href = "/" + lastMainViewHash();
}

function groupTabs() {
  return Array.from(document.querySelectorAll(".settings-tab"));
}

function groupNames() {
  return groupTabs().map((tab) => tab.dataset.group);
}

/** Depolama kapali/dolu olabilir: okuma da yazma da sessizce basarisiz olur. */
function rememberedGroup() {
  try {
    return localStorage.getItem(GROUP_KEY) || "";
  } catch (err) {
    return "";
  }
}

function rememberGroup(name) {
  try {
    localStorage.setItem(GROUP_KEY, name);
  } catch (err) {
    /* gizli sekme ya da kapali depolama: hatirlamak zorunlu degil */
  }
}

/** Secili grubu gosterir; digerleri gizlenir, sekme vurgusu tasinir. */
function showGroup(name, options) {
  const names = groupNames();
  const target = names.includes(name) ? name : names[0] || DEFAULT_GROUP;
  groupTabs().forEach((tab) => {
    const on = tab.dataset.group === target;
    tab.classList.toggle("on", on);
    tab.setAttribute("aria-current", on ? "page" : "false");
  });
  document.querySelectorAll(".settings-group").forEach((section) => {
    section.hidden = section.dataset.group !== target;
  });
  rememberGroup(target);
  if (!options || options.updateHash !== false) {
    // Adres cubugu gecmisi sismesin: aynı grup icin yeni giris acilmaz.
    if (window.location.hash !== "#" + target) {
      window.history.replaceState(null, "", "#" + target);
    }
  }
  return target;
}

function bindGroups() {
  groupTabs().forEach((tab) => {
    tab.addEventListener("click", () => showGroup(tab.dataset.group));
  });
  window.addEventListener("hashchange", () => {
    showGroup(window.location.hash.replace("#", ""), { updateHash: false });
  });
  const wanted = window.location.hash.replace("#", "") || rememberedGroup() || DEFAULT_GROUP;
  showGroup(wanted);
}

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
  fillTeams(settings);
  fillCopilot(settings);
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

/** Jira ve Ag alanlari TEK yuke gider: iki kartin Kaydet dugmesi de ayni
 *  payload'u yazar, yalnizca durum satiri farkli kutuya duser. */
async function saveSettings(statusId) {
  const status = document.getElementById(statusId || "status");
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

async function testConnection(statusId) {
  const status = document.getElementById(statusId || "status");
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
function renderDiagnosis(result, boxId) {
  const box = document.getElementById(boxId || "diagnosis");
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

async function diagnose(statusId, boxId) {
  const status = document.getElementById(statusId || "status");
  const box = document.getElementById(boxId || "diagnosis");
  box.hidden = true;
  setStatus(status, "Teşhis çalışıyor, her adım en çok beş saniye sürer...", null);
  try {
    await api("/api/settings", { method: "PUT", body: JSON.stringify(collectForm()) });
    document.getElementById("secret").value = "";
    const result = await api("/api/settings/diagnose", { method: "POST" });
    renderDiagnosis(result, boxId || "diagnosis");
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

// --- Teams --------------------------------------------------------------
//
// Sablonlar ve adres defteri burada yonetilir; rehber Outlook'tan cekilir.
// Mesajin kendisi kayit cekmecesinden acilir, buradaki ayarlar oraya besler.

const teamsState = { templates: [], contacts: [], placeholders: [], supported: true };

function teamsRow(children) {
  const row = document.createElement("div");
  row.className = "teams-row";
  children.forEach((child) => row.appendChild(child));
  return row;
}

function textBox(value, placeholder) {
  const box = document.createElement("input");
  box.type = "text";
  box.value = value || "";
  if (placeholder) box.placeholder = placeholder;
  return box;
}

function smallButton(label, kind, onClick) {
  const button = document.createElement("button");
  button.className = "small" + (kind ? " " + kind : "");
  button.textContent = label;
  button.addEventListener("click", onClick);
  return button;
}

async function loadTeamsLists() {
  const [templates, contacts] = await Promise.all([
    api("/api/templates"),
    api("/api/contacts?limit=50"),
  ]);
  teamsState.templates = templates.templates || [];
  teamsState.placeholders = templates.placeholders || [];
  teamsState.contacts = contacts.contacts || [];
  document.getElementById("teams-topic").value = templates.topic_format || "{key}";
  renderTemplates();
  renderContacts();
}

/** Rehber dugmesi ve son yenileme zamani; Windows disinda dugme pasif. */
function fillTeams(settings) {
  teamsState.supported = settings.mail_supported !== false;
  document.getElementById("teams-gal").disabled = !teamsState.supported;
  document.getElementById("teams-gal-unsupported").hidden = teamsState.supported;
  const when = settings["teams.gal_synced_at"];
  document.getElementById("teams-gal-when").textContent = when
    ? "Son yenileme: " + galStamp(when)
    : "Rehber hiç çekilmedi.";

}

/** ISO damgayi yerel saatle "GG.AA.YYYY SS:dd" yazar. */
function galStamp(iso) {
  const moment = new Date(iso);
  if (isNaN(moment.getTime())) return String(iso);
  const pad = (value) => String(value).padStart(2, "0");
  return (
    `${pad(moment.getDate())}.${pad(moment.getMonth() + 1)}.${moment.getFullYear()} ` +
    `${pad(moment.getHours())}:${pad(moment.getMinutes())}`
  );
}

/** Kurum rehberini ceker; sonuc sayilari durum satirinda yazar. */
async function importGal() {
  const status = contactsStatus();
  setStatus(status, "Kurum rehberi okunuyor, büyük rehberde birkaç saniye sürebilir...", null);
  try {
    const data = await api("/api/contacts/import-gal", { method: "POST" });
    const result = data.result || {};
    await loadTeamsLists();
    await loadSettings();
    setStatus(
      status,
      `${result.imported || 0} kişi eklendi · ${result.updated || 0} kişi güncellendi · ` +
        `${result.lists || 0} dağıtım listesi · ${result.total || 0} giriş · ` +
        `${((result.ms || 0) / 1000).toFixed(1)} sn`,
      "ok"
    );
  } catch (err) {
    setStatus(status, "Kurum rehberi alınamadı: " + err.message, "error");
  }
}

function renderTemplates() {
  const box = document.getElementById("teams-templates");
  box.textContent = "";
  if (!teamsState.templates.length) {
    box.appendChild(emptyNote("Henüz şablon yok."));
  }
  teamsState.templates.forEach((template, index) => {
    const nameBox = textBox(template.name, "Şablon adı");
    const bodyBox = document.createElement("textarea");
    bodyBox.rows = 2;
    bodyBox.value = template.body;

    const mark = document.createElement("label");
    mark.className = "checkbox";
    const check = document.createElement("input");
    check.type = "radio";
    check.name = "template-default";
    check.checked = !!template.is_default;
    check.addEventListener("change", () => saveTemplate(template.id, { is_default: true }));
    const markText = document.createElement("span");
    markText.textContent = "Varsayılan";
    mark.appendChild(check);
    mark.appendChild(markText);

    const row = teamsRow([
      nameBox,
      mark,
      smallButton("↑", "", () => moveTemplate(index, -1)),
      smallButton("↓", "", () => moveTemplate(index, 1)),
      smallButton("Kaydet", "", () =>
        saveTemplate(template.id, { name: nameBox.value, body: bodyBox.value })
      ),
      smallButton("Sil", "danger", () => dropTemplate(template)),
    ]);
    const wrap = document.createElement("div");
    wrap.className = "teams-entry";
    wrap.appendChild(row);
    wrap.appendChild(bodyBox);
    box.appendChild(wrap);
  });

  const hint = document.getElementById("teams-placeholders");
  hint.textContent =
    "Yer tutucular: " +
    teamsState.placeholders.map((item) => `${item.token} (${item.label})`).join(", ") +
    ". Bilinmeyen yer tutucu boş kalır.";
}

function renderContacts() {
  const box = document.getElementById("teams-contacts");
  box.textContent = "";
  if (!teamsState.contacts.length) {
    box.appendChild(
      emptyNote(
        "Adres defteri boş: kayıtlara kişi ekledikçe dolar, " +
          "ya da kurum rehberini Outlook'tan çekin."
      )
    );
    return;
  }
  teamsState.contacts.forEach((contact) => {
    const nameBox = textBox(contact.name, "Ad");
    const emailBox = textBox(contact.email, "ornek@example.com");
    const row = teamsRow([nameBox, emailBox]);
    if (contact.kind === "list") {
      const badge = document.createElement("span");
      badge.className = "badge";
      badge.textContent = "liste";
      badge.title = "Dağıtım listesi";
      row.appendChild(badge);
    }
    if (contact.source === "gal") {
      const badge = document.createElement("span");
      badge.className = "badge";
      badge.textContent = "rehber";
      badge.title = "Outlook kurum rehberinden geldi";
      row.appendChild(badge);
    }
    row.appendChild(
      smallButton("Kaydet", "", () =>
        saveContact(contact.email, { name: nameBox.value, email: emailBox.value })
      )
    );
    row.appendChild(smallButton("Sil", "danger", () => dropContact(contact)));
    box.appendChild(row);
  });
}

function emptyNote(text) {
  const note = document.createElement("p");
  note.className = "hint";
  note.textContent = text;
  return note;
}

function teamsStatus() {
  return document.getElementById("teams-status");
}

/** Kisiler grubu ayri bir karttir: rehber ve kisi islemleri oraya yazar. */
function contactsStatus() {
  return document.getElementById("contacts-status");
}

async function contactsAction(run, message) {
  try {
    await run();
    await loadTeamsLists();
    setStatus(contactsStatus(), message, "ok");
  } catch (err) {
    setStatus(contactsStatus(), err.message, "error");
  }
}

async function teamsAction(run, message) {
  try {
    await run();
    await loadTeamsLists();
    setStatus(teamsStatus(), message, "ok");
  } catch (err) {
    setStatus(teamsStatus(), err.message, "error");
  }
}

function saveTemplate(id, payload) {
  return teamsAction(
    () => api(`/api/templates/${id}`, { method: "PUT", body: JSON.stringify(payload) }),
    "Şablon kaydedildi."
  );
}

function moveTemplate(index, delta) {
  const target = index + delta;
  if (target < 0 || target >= teamsState.templates.length) return;
  const moved = teamsState.templates[index];
  const other = teamsState.templates[target];
  return teamsAction(async () => {
    await api(`/api/templates/${moved.id}`, {
      method: "PUT",
      body: JSON.stringify({ position: other.position }),
    });
    await api(`/api/templates/${other.id}`, {
      method: "PUT",
      body: JSON.stringify({ position: moved.position }),
    });
  }, "Şablon sırası değişti.");
}

function dropTemplate(template) {
  if (!confirm(`"${template.name}" şablonu silinsin mi?`)) return;
  teamsAction(
    () => api(`/api/templates/${template.id}`, { method: "DELETE" }),
    "Şablon silindi."
  );
}

function addTemplate() {
  const name = document.getElementById("template-name");
  const body = document.getElementById("template-body");
  teamsAction(async () => {
    await api("/api/templates", {
      method: "POST",
      body: JSON.stringify({ name: name.value, body: body.value }),
    });
    name.value = "";
    body.value = "";
  }, "Şablon eklendi.");
}

function saveContact(email, payload) {
  return contactsAction(
    () =>
      api(`/api/contacts/${encodeURIComponent(email)}`, {
        method: "PUT",
        body: JSON.stringify(payload),
      }),
    "Kişi kaydedildi."
  );
}

function dropContact(contact) {
  const label = contact.name || contact.email;
  if (!confirm(`"${label}" adres defterinden ve bütün kayıtlardan silinsin mi?`)) return;
  contactsAction(
    () => api(`/api/contacts/${encodeURIComponent(contact.email)}`, { method: "DELETE" }),
    "Kişi silindi."
  );
}

function saveTeams() {
  const topic = document.getElementById("teams-topic").value.trim() || "{key}";
  const payload = { "teams.topic_format": topic };
  teamsAction(
    () => api("/api/settings", { method: "PUT", body: JSON.stringify(payload) }),
    "Teams ayarları kaydedildi."
  );
}


// --- Copilot CLI --------------------------------------------------------
//
// Kart Copilot'un kendisini yapilandirir: nerede duruyor, hangi vekilden
// cikiyor, hangi modeller sirayla denenecek. Sinama ucu `/api/copilot/sina`
// kucucuk bir istek atar ve calisan modeli "son calisan" olarak saklar.

function copilotField(id) {
  return document.getElementById(id);
}

function copilotStatus() {
  return document.getElementById("copilot-status");
}

function fillCopilot(settings) {
  copilotField("copilot-yol").value = settings["copilot.yolu"] || "";
  copilotField("copilot-proxy").value = settings["copilot.proxy"] || "";
  copilotField("copilot-modeller").value = modelListText(settings["copilot.modeller"]);
  const son = settings["copilot.son_model"] || "";
  copilotField("copilot-son-model").textContent = son
    ? "Son çalışan model: " + son
    : "Copilot henüz hiç çalıştırılmadı.";
  // Otomatik bulunan yol ipucu olarak durur: kullanici ne calistigini gorur.
  const sonYol = settings["copilot.yolu_son"] || "";
  copilotField("copilot-sonuc").textContent = sonYol ? "Son bulunan: " + sonYol : "";
  fillDuzelt(settings);
}

// --- Metin duzeltme ("Düzelt" dugmesi) ---------------------------------
//
// Gomulu sablon sunucudan gelir (`copilot_duzelt_sablon_varsayilan`):
// "Şablonu varsayılana döndür" onu kutuya yazar, kaydedilen bos deger de
// gomulu sablon demektir.

let duzeltVarsayilanSablon = "";

function fillDuzelt(settings) {
  duzeltVarsayilanSablon = settings["copilot_duzelt_sablon_varsayilan"] || "";
  copilotField("copilot-duzelt-acik").checked = settings["copilot.duzelt_acik"] !== "0";
  copilotField("copilot-duzelt-ton").value =
    settings["copilot.duzelt_ton"] === "resmi" ? "resmi" : "notr";
  const sablon = settings["copilot.duzelt_sablon"] || "";
  copilotField("copilot-duzelt-sablon").value = sablon || duzeltVarsayilanSablon;
  // Gomulu sablon oldugu gibi duruyorsa ayara bos yazilir (bkz. collectDuzelt).
  copilotField("copilot-duzelt-sablon").dataset.ozel = sablon ? "1" : "";
}

function collectDuzelt() {
  const kutu = copilotField("copilot-duzelt-sablon");
  const yazilan = kutu.value.trim();
  return {
    "copilot.duzelt_acik": copilotField("copilot-duzelt-acik").checked ? "1" : "0",
    "copilot.duzelt_ton": copilotField("copilot-duzelt-ton").value,
    // Gomulu sablonun aynisi kaydedilmez: sablon degisirse kullanici yeni
    // surumu kendiliginden alsin.
    "copilot.duzelt_sablon":
      !yazilan || yazilan === duzeltVarsayilanSablon.trim() ? "" : kutu.value,
  };
}

async function saveDuzelt() {
  const status = document.getElementById("copilot-duzelt-status");
  try {
    const data = await api("/api/settings", {
      method: "PUT",
      body: JSON.stringify(collectDuzelt()),
    });
    fillDuzelt(data.settings || {});
    setStatus(status, "Metin düzeltme ayarları kaydedildi.", "ok");
  } catch (err) {
    setStatus(status, err.message, "error");
  }
}

function resetDuzeltTemplate() {
  copilotField("copilot-duzelt-sablon").value = duzeltVarsayilanSablon;
  setStatus(
    document.getElementById("copilot-duzelt-status"),
    "Gömülü şablon yazıldı; Kaydet'e basınca geçerli olur.",
    null
  );
}

/** Ayar JSON listesi de olabilir, virgullu metin de: ikisi de okunur. */
function modelListText(value) {
  const text = String(value || "").trim();
  if (!text) return "";
  if (text.startsWith("[")) {
    try {
      const parsed = JSON.parse(text);
      return Array.isArray(parsed) ? parsed.join(", ") : text;
    } catch (err) {
      return text;
    }
  }
  return text;
}

function collectCopilot() {
  const modeller = copilotField("copilot-modeller")
    .value.split(",")
    .map((ad) => ad.trim())
    .filter(Boolean);
  return {
    "copilot.yolu": copilotField("copilot-yol").value.trim(),
    "copilot.proxy": copilotField("copilot-proxy").value.trim(),
    "copilot.modeller": JSON.stringify(modeller),
  };
}

async function saveCopilot() {
  try {
    const data = await api("/api/settings", {
      method: "PUT",
      body: JSON.stringify(collectCopilot()),
    });
    fillCopilot(data.settings || {});
    setStatus(copilotStatus(), "Copilot ayarları kaydedildi.", "ok");
  } catch (err) {
    setStatus(copilotStatus(), err.message, "error");
  }
}

/** Sinama: alandaki yol KAYDEDILMEDEN denenebilsin diye uca yollanir. */
async function testCopilot() {
  const button = copilotField("copilot-test");
  const box = copilotField("copilot-sonuc");
  button.disabled = true;
  box.textContent = "Sınanıyor...";
  setStatus(copilotStatus(), "Copilot CLI sınanıyor...", null);
  try {
    const sonuc = await api("/api/copilot/sina", {
      method: "POST",
      body: JSON.stringify({ yol: copilotField("copilot-yol").value.trim() }),
    });
    box.textContent = sonuc.mesaj || "";
    setStatus(copilotStatus(), sonuc.mesaj || "", sonuc.calisiyor ? "ok" : "error");
    if (sonuc.calisiyor) {
      copilotField("copilot-son-model").textContent = "Son çalışan model: " + sonuc.model;
    }
  } catch (err) {
    box.textContent = err.message;
    setStatus(copilotStatus(), err.message, "error");
  } finally {
    button.disabled = false;
  }
}

document.addEventListener("DOMContentLoaded", () => {
  bindShell();
  bindGroups();
  document.getElementById("settings-back").addEventListener("click", goBackToMain);
  document.getElementById("mode").addEventListener("change", applyModeVisibility);
  document.getElementById("auth-type").addEventListener("change", applyModeVisibility);
  document.getElementById("save").addEventListener("click", () => saveSettings("status"));
  document.getElementById("test").addEventListener("click", () => testConnection("status"));
  document.getElementById("diagnose").addEventListener("click", () => diagnose("status", "diagnosis"));
  // Ag karti ayni yuku yazar, sonucu kendi kutusuna koyar.
  document.getElementById("net-save").addEventListener("click", () => saveSettings("net-status"));
  document
    .getElementById("net-diagnose")
    .addEventListener("click", () => diagnose("net-status", "net-diagnosis"));
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
  document.getElementById("template-add").addEventListener("click", addTemplate);
  document.getElementById("teams-gal").addEventListener("click", importGal);
  document.getElementById("teams-save").addEventListener("click", saveTeams);
  document.getElementById("copilot-save").addEventListener("click", saveCopilot);
  document.getElementById("copilot-test").addEventListener("click", testCopilot);
  document.getElementById("copilot-duzelt-save").addEventListener("click", saveDuzelt);
  document
    .getElementById("copilot-duzelt-varsayilan")
    .addEventListener("click", resetDuzeltTemplate);
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
  loadTeamsLists().catch((err) => setStatus(teamsStatus(), err.message, "error"));
});
