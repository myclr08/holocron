// Ayarlar -> "E-posta şablonları" kartı.
//
// settings.js'ten ayrı durur: bu kartın kendi ucu (`/api/mail-templates`), kendi
// sırası ve kendi gönderim kipi var. Kendi DOMContentLoaded dinleyicisini kurar;
// aynı olaya birden çok dinleyici takılabilir, dosyalar birbirine dokunmaz.

const mailSendSettings = { templates: [], placeholders: [], mode: "display" };

function mailSendStatus() {
  return document.getElementById("mailsend-status");
}

function mailSendBox(tag, className) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  return node;
}

function mailSendInput(value, placeholder) {
  const box = document.createElement("input");
  box.type = "text";
  box.value = value || "";
  if (placeholder) box.placeholder = placeholder;
  return box;
}

function mailSendButton(label, kind, onClick) {
  const button = document.createElement("button");
  button.className = "small" + (kind ? " " + kind : "");
  button.textContent = label;
  button.addEventListener("click", onClick);
  return button;
}

function mailSendCheck(labelText, checked, onChange) {
  const wrap = document.createElement("label");
  wrap.className = "checkbox";
  const input = document.createElement("input");
  input.type = "checkbox";
  input.checked = !!checked;
  input.addEventListener("change", () => onChange(input.checked));
  const text = document.createElement("span");
  text.textContent = labelText;
  wrap.appendChild(input);
  wrap.appendChild(text);
  return wrap;
}

async function loadMailTemplates() {
  const data = await api("/api/mail-templates");
  mailSendSettings.templates = data.templates || [];
  mailSendSettings.placeholders = data.placeholders || [];
  mailSendSettings.mode = data.mode === "send" ? "send" : "display";
  const radio = document.querySelector(
    `#mailsend-mode input[value="${mailSendSettings.mode}"]`
  );
  if (radio) radio.checked = true;
  renderMailTemplates();
}

function renderMailTemplates() {
  const box = document.getElementById("mailsend-templates");
  box.textContent = "";
  if (!mailSendSettings.templates.length) {
    const note = mailSendBox("p", "hint");
    note.textContent = "Henüz e-posta şablonu yok.";
    box.appendChild(note);
  }

  mailSendSettings.templates.forEach((template, index) => {
    const nameBox = mailSendInput(template.name, "Şablon adı");
    const toBox = mailSendInput(template.to_addresses, "Kime: ornek@example.com");
    const ccBox = mailSendInput(template.cc_addresses, "CC: ekip@example.com");
    const subjectBox = mailSendInput(template.subject, "Konu");
    const bodyBox = document.createElement("textarea");
    bodyBox.rows = 3;
    bodyBox.value = template.body || "";

    const save = () =>
      saveMailTemplate(template.id, {
        name: nameBox.value,
        to_addresses: toBox.value,
        cc_addresses: ccBox.value,
        subject: subjectBox.value,
        body: bodyBox.value,
      });

    const row = mailSendBox("div", "teams-row");
    [nameBox, toBox, ccBox].forEach((node) => row.appendChild(node));
    row.appendChild(mailSendButton("↑", "", () => moveMailTemplate(index, -1)));
    row.appendChild(mailSendButton("↓", "", () => moveMailTemplate(index, 1)));
    row.appendChild(mailSendButton("Kaydet", "", save));
    row.appendChild(mailSendButton("Sil", "danger", () => dropMailTemplate(template)));

    const options = mailSendBox("div", "teams-row");
    options.appendChild(
      mailSendCheck("Excel ekle", template.attach_excel, (checked) =>
        saveMailTemplate(template.id, { attach_excel: checked })
      )
    );
    options.appendChild(
      mailSendCheck("Gövdeye tablo ekle", template.inline_table, (checked) =>
        saveMailTemplate(template.id, { inline_table: checked })
      )
    );

    const wrap = mailSendBox("div", "teams-entry");
    wrap.appendChild(row);
    wrap.appendChild(subjectBox);
    wrap.appendChild(bodyBox);
    wrap.appendChild(options);
    box.appendChild(wrap);
  });

  const hint = document.getElementById("mailsend-placeholders");
  hint.textContent =
    "Yer tutucular: " +
    mailSendSettings.placeholders.map((item) => `${item.token} (${item.label})`).join(", ") +
    ". Bilinmeyen yer tutucu boş kalır; {tablo} yalnız gövdede çalışır.";
}

async function mailSendAction(run, message) {
  try {
    await run();
    await loadMailTemplates();
    setStatus(mailSendStatus(), message, "ok");
  } catch (err) {
    setStatus(mailSendStatus(), err.message, "error");
  }
}

function saveMailTemplate(id, payload) {
  return mailSendAction(
    () => api(`/api/mail-templates/${id}`, { method: "PUT", body: JSON.stringify(payload) }),
    "Şablon kaydedildi."
  );
}

function moveMailTemplate(index, delta) {
  const target = index + delta;
  if (target < 0 || target >= mailSendSettings.templates.length) return;
  const ids = mailSendSettings.templates.map((item) => item.id);
  const moved = ids.splice(index, 1)[0];
  ids.splice(target, 0, moved);
  return mailSendAction(
    () =>
      api("/api/mail-templates/reorder", {
        method: "POST",
        body: JSON.stringify({ ids: ids }),
      }),
    "Şablon sırası değişti."
  );
}

function dropMailTemplate(template) {
  if (!confirm(`"${template.name}" şablonu silinsin mi?`)) return;
  mailSendAction(
    () => api(`/api/mail-templates/${template.id}`, { method: "DELETE" }),
    "Şablon silindi."
  );
}

function addMailTemplate() {
  const fields = {
    name: document.getElementById("mailsend-name"),
    subject: document.getElementById("mailsend-new-subject"),
    to_addresses: document.getElementById("mailsend-new-to"),
    cc_addresses: document.getElementById("mailsend-new-cc"),
    body: document.getElementById("mailsend-new-body"),
  };
  const payload = {};
  Object.entries(fields).forEach(([key, node]) => (payload[key] = node.value));
  mailSendAction(async () => {
    await api("/api/mail-templates", { method: "POST", body: JSON.stringify(payload) });
    Object.values(fields).forEach((node) => (node.value = ""));
  }, "Şablon eklendi.");
}

function saveMailSendMode() {
  const chosen = document.querySelector("#mailsend-mode input:checked");
  const mode = chosen ? chosen.value : "display";
  mailSendAction(
    () =>
      api("/api/settings", {
        method: "PUT",
        body: JSON.stringify({ "mailsend.mode": mode }),
      }),
    mode === "send" ? "Kip: doğrudan gönder." : "Kip: Outlook'ta aç."
  );
}

document.addEventListener("DOMContentLoaded", () => {
  document.getElementById("mailsend-add").addEventListener("click", addMailTemplate);
  document.getElementById("mailsend-save").addEventListener("click", saveMailSendMode);
  api("/api/settings")
    .then((data) => {
      const settings = data.settings || {};
      document.getElementById("mailsend-unsupported").hidden =
        settings.mail_supported !== false;
    })
    .catch(() => {});
  loadMailTemplates().catch((err) => setStatus(mailSendStatus(), err.message, "error"));
});
