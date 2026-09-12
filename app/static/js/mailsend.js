// "E-posta ile gonder": grubun kayitlarini secili sutunlarla Excel'e cevirip
// sablonlu bir postaya ekler, Outlook'ta acar ya da gonderir.
//
// Ayri dosyada durur: app.js zaten grid, kanban ve cekmeceyi tasiyor. Grid'deki
// secim (onay kutusu sutunu) app.js'in `state.selectedKeys` kumesinde yasar,
// buradan yalnizca okunur.
//
// Govde HAM tutulur: metin kutusunda `{tablo}` yazar, onizleme ve gonderim onu
// sunucuda tabloya cevirir. Boylece pencerede duzenlenen metin ile giden posta
// hep ayni kaynaktan uretilir.

const mailSend = {
  templates: [],
  template: null,
  mode: "display",
  to: [],
  cc: [],
  columns: [],
  preview: null,
};

/** ISO damga -> "GG.AA.YYYY SS:dd" (yerel saat). */
function mailStamp(iso) {
  const moment = new Date(iso);
  if (isNaN(moment.getTime())) return String(iso || "");
  const pad = (value) => String(value).padStart(2, "0");
  return (
    `${pad(moment.getDate())}.${pad(moment.getMonth() + 1)}.${moment.getFullYear()} ` +
    `${pad(moment.getHours())}:${pad(moment.getMinutes())}`
  );
}

async function mailSendModal() {
  if (!state.group) return;
  let data;
  try {
    data = await api("/api/mail-templates");
  } catch (err) {
    fail(err);
    return;
  }
  mailSend.templates = data.templates || [];
  mailSend.mode = data.mode === "send" ? "send" : "display";
  if (!mailSend.templates.length) {
    toast(
      "E-posta ile gönder",
      "Önce Ayarlar → E-posta şablonları altında bir şablon tanımlayın.",
      "error"
    );
    return;
  }

  mailSend.to = [];
  mailSend.cc = [];
  mailSend.preview = null;

  const picked = selectedKeyList();

  const templateBox = h("select", { id: "mailsend-template" }, []);
  mailSend.templates.forEach((template) => {
    templateBox.appendChild(h("option", { value: String(template.id), text: template.name }));
  });

  const subjectInput = h("input", { type: "text", id: "mailsend-subject" });
  const bodyInput = h("textarea", { id: "mailsend-body", rows: "8" });

  const columnList = h("div", { class: "export-columns", id: "mailsend-columns" }, []);
  const columnBoxes = state.columns.map((column) => {
    const input = h("input", { type: "checkbox" });
    input.checked = true;
    columnList.appendChild(
      h("label", { class: "checkbox" }, [input, document.createTextNode(column.name)])
    );
    return { id: column.id, input: input };
  });

  const pickedInput = h("input", { type: "checkbox", id: "mailsend-picked" });
  pickedInput.checked = picked.length > 0;
  const viewInput = h("input", { type: "checkbox", id: "mailsend-view" });
  viewInput.checked = true;
  const excelInput = h("input", { type: "checkbox", id: "mailsend-excel" });
  excelInput.checked = true;
  const tableInput = h("input", { type: "checkbox", id: "mailsend-table" });
  tableInput.checked = true;

  const footLine = h("p", { class: "hint", id: "mailsend-foot", text: "Hazırlanıyor..." });
  const historyBox = h("div", { class: "mail-sent", id: "mailsend-history" }, []);
  const previewFrame = h("iframe", {
    id: "mailsend-preview",
    title: "Önizleme",
    sandbox: "",
  });
  previewFrame.hidden = true;

  const bodyTab = h("button", { class: "tab on", id: "mailsend-tab-body", text: "Gövde" });
  const viewTab = h("button", { class: "tab", id: "mailsend-tab-preview", text: "Önizleme" });

  // Adres kutulari ortak bilesen (addressbox.js): bosluk ayirici degildir,
  // tamamlama listesinden secim TEK cip eder (ad gorunur, adres gider).
  const toField = createAddressBox({
    entries: mailSend.to,
    id: "mailsend-to",
    onChange: () => refresh(true),
  });
  const ccField = createAddressBox({
    entries: mailSend.cc,
    id: "mailsend-cc",
    onChange: () => refresh(true),
  });

  const payload = () => ({
    template_id: mailSend.template ? mailSend.template.id : null,
    columns: columnBoxes.filter((item) => item.input.checked).map((item) => item.id),
    keys: picked.length && pickedInput.checked ? picked : null,
    apply_view: viewInput.checked,
    q: viewInput.checked ? state.query : "",
    sort: viewInput.checked && state.sort ? state.sort.field : "",
    dir: viewInput.checked && state.sort ? state.sort.dir : "",
    subject: subjectInput.value,
    body: bodyInput.value,
    inline_table: tableInput.checked,
  });

  const refresh = async (quiet) => {
    try {
      const view = await api(`/api/groups/${state.activeId}/mail-preview`, {
        method: "POST",
        body: JSON.stringify(payload()),
      });
      mailSend.preview = view;
      footLine.textContent =
        `${view.count} kayıt` + (excelInput.checked ? ` · ${view.file_name}` : " · ek yok");
      previewFrame.srcdoc = view.html;
      return view;
    } catch (err) {
      mailSend.preview = null;
      footLine.textContent = err.message;
      if (!quiet) fail(err);
      return null;
    }
  };

  const pickTemplate = async (templateId) => {
    const template = mailSend.templates.find((item) => String(item.id) === String(templateId));
    if (!template) return;
    mailSend.template = template;
    templateBox.value = String(template.id);
    subjectInput.value = template.subject || "";
    bodyInput.value = template.body || "";
    excelInput.checked = !!template.attach_excel;
    tableInput.checked = !!template.inline_table;
    toField.setText(template.to_addresses);
    ccField.setText(template.cc_addresses);
    await refresh(true);
  };

  templateBox.addEventListener("change", () => pickTemplate(templateBox.value));
  [subjectInput, bodyInput].forEach((node) =>
    node.addEventListener("blur", () => refresh(true))
  );
  [pickedInput, viewInput, excelInput, tableInput].forEach((node) =>
    node.addEventListener("change", () => refresh(true))
  );
  columnBoxes.forEach((item) => item.input.addEventListener("change", () => refresh(true)));

  const showTab = (preview) => {
    bodyTab.classList.toggle("on", !preview);
    viewTab.classList.toggle("on", preview);
    bodyInput.hidden = preview;
    previewFrame.hidden = !preview;
    if (preview) refresh(true);
  };
  bodyTab.addEventListener("click", () => showTab(false));
  viewTab.addEventListener("click", () => showTab(true));

  const body = h("div", { class: "mailsend" }, [
    h("div", { class: "row" }, [
      h("div", { class: "field" }, [h("label", { text: "Şablon" }), templateBox]),
      h("div", { class: "field" }, [
        h("label", { text: "Grubun varsayılanı" }),
        h("button", {
          class: "small",
          id: "mailsend-default",
          text: "Bu şablonu bu gruba bağla",
          onclick: async () => {
            try {
              await api(`/api/groups/${state.activeId}/mail-template`, {
                method: "PUT",
                body: JSON.stringify({
                  template_id: mailSend.template ? mailSend.template.id : null,
                }),
              });
              toast("E-posta ile gönder", "Şablon bu gruba bağlandı.", "ok");
            } catch (err) {
              fail(err);
            }
          },
        }),
      ]),
    ]),
    h("div", { class: "field" }, [h("label", { text: "Kime" }), toField]),
    h("div", { class: "field" }, [h("label", { text: "CC" }), ccField]),
    h("div", { class: "field" }, [h("label", { text: "Konu" }), subjectInput]),
    h("div", { class: "tabs" }, [bodyTab, viewTab]),
    bodyInput,
    previewFrame,
    h("p", {
      class: "hint",
      text: "Yer tutucular: {grup}, {tarih}, {adet}, {jql}, {tablo}. Bilinmeyen yer tutucu boş kalır.",
    }),
    h("div", { class: "export-head" }, [
      h("span", { class: "hint", text: "Tabloya ve Excel'e gidecek sütunlar" }),
      h("button", {
        class: "small",
        text: "Tümünü seç",
        onclick: () => {
          columnBoxes.forEach((item) => (item.input.checked = true));
          refresh(true);
        },
      }),
      h("button", {
        class: "small",
        text: "Temizle",
        onclick: () => {
          columnBoxes.forEach((item) => (item.input.checked = false));
          refresh(true);
        },
      }),
    ]),
    columnList,
    picked.length
      ? h("label", { class: "checkbox" }, [
          pickedInput,
          document.createTextNode(`Yalnız seçili ${picked.length} kayıt`),
        ])
      : null,
    h("label", { class: "checkbox" }, [
      viewInput,
      document.createTextNode("Görünen süzgeç ve sıralamayı uygula"),
    ]),
    h("label", { class: "checkbox" }, [excelInput, document.createTextNode("Excel ekle")]),
    h("label", { class: "checkbox" }, [
      tableInput,
      document.createTextNode("Gövdeye tablo ekle"),
    ]),
    footLine,
    h("h3", { class: "mail-sent-head", text: "Gönderilenler" }),
    historyBox,
  ]);

  const start = async (mode) => {
    // Kutuda yarim kalmis metin varsa once cipe cevrilir.
    toField.commit();
    ccField.commit();
    const view = await refresh(false);
    if (!view) return;
    if (!toField.entries.length) {
      toast("E-posta ile gönder", "Kime alanı boş olamaz.", "error");
      return;
    }
    try {
      const sent = await api(`/api/groups/${state.activeId}/mail-send`, {
        method: "POST",
        body: JSON.stringify({
          ...payload(),
          to: addressEmails(toField.entries),
          cc: addressEmails(ccField.entries),
          html: view.html,
          attach_excel: excelInput.checked,
          mode: mode,
        }),
      });
      toast(
        "E-posta ile gönder",
        sent.mode === "send"
          ? `${sent.count} kayıt gönderildi.`
          : `${sent.count} kayıt Outlook'ta açıldı; Gönder'e siz basın.`,
        "ok"
      );
      await loadSends(historyBox);
    } catch (err) {
      fail(err);
    }
  };

  const buttons = [
    { label: "Vazgeç", onClick: closeModal },
    { label: "Outlook'ta aç", kind: "primary", onClick: () => start("display") },
  ];
  if (mailSend.mode === "send") {
    buttons.push({ label: "Gönder", kind: "primary", onClick: () => start("send") });
  }

  openModal("E-posta ile gönder", body, buttons, { wide: true });

  // Grubun varsayilanini sunucu cozer: template_id vermeden sorulur
  // (grup bagi yoksa listenin ilki doner).
  let firstId = mailSend.templates[0].id;
  try {
    const probe = await api(`/api/groups/${state.activeId}/mail-preview`, {
      method: "POST",
      body: JSON.stringify({ apply_view: false }),
    });
    if (probe.template_id) firstId = probe.template_id;
  } catch (err) {
    // Onizleme alinamadi; listenin ilk sablonuyla acilir.
  }
  await pickTemplate(firstId);
  loadSends(historyBox);
}

/** "Gonderilenler": tarih, konu, alici sayisi, kayit adedi. */
async function loadSends(box) {
  try {
    const data = await api(`/api/groups/${state.activeId}/mail-sends?limit=10`);
    clear(box);
    const sends = data.sends || [];
    if (!sends.length) {
      box.appendChild(h("p", { class: "hint", text: "Bu gruptan henüz gönderim yok." }));
      return;
    }
    sends.forEach((item) => {
      box.appendChild(
        h("div", { class: "mail-sent-line" }, [
          h("span", { class: "when", text: mailStamp(item.sent_at) }),
          h("span", { class: "subject", text: item.subject || "(konusuz)" }),
          h("span", {
            class: "badge",
            text: `${item.to_count} alıcı · ${item.issue_count} kayıt`,
          }),
          h("span", {
            class: "badge",
            text: item.mode === "send" ? "gönderildi" : "açıldı",
          }),
        ])
      );
    });
  } catch (err) {
    clear(box);
    box.appendChild(h("p", { class: "hint", text: err.message }));
  }
}
