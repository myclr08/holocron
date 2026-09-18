// Görüşme notları: takip şeridi, notlar sekmesi, not çekmecesi.
// Aramalar ekranının dördüncü sekmesi; ortak yardımcılar (api, h, el, clear,
// stamp, toast, fail) app.js ve common.js içinden gelir, çerçeve ve CDN yok.

// Canlı rozet: kaydederken saniyede bir, boştayken seyrek yenilenir.
const GORUSME_TICK_MS = 1000;
const GORUSME_IDLE_MS = 5000;

const gorusmeState = {
  notlar: [],
  serit: null,
  supported: true,
  whisperVar: true,
  drawerId: null,
  timer: null,
  // Aynı anda yalnızca en son isteğin yanıtı çizilir.
  request: 0,
};

// --- veri ----------------------------------------------------------------

async function loadGorusme() {
  const token = ++gorusmeState.request;
  const params = new URLSearchParams({ days: String(callsState.days) });
  if (callsState.query) params.set("q", callsState.query);
  try {
    const data = await api("/api/gorusme/view?" + params.toString());
    if (token !== gorusmeState.request) return;
    gorusmeState.notlar = data.notlar || [];
    gorusmeState.serit = data.serit || null;
    gorusmeState.supported = data.supported !== false;
    gorusmeState.whisperVar = data.whisper_var !== false;
    renderGorusmeTrack();
    renderGorusmeList();
  } catch (err) {
    fail(err);
  }
}

/** Şerit tek başına: canlı rozet bütün listeyi yeniden çekmeden ilerler. */
async function refreshGorusmeTrack() {
  try {
    const serit = await api("/api/gorusme/serit");
    const oncekiHazir = (gorusmeState.serit || {}).hazir;
    gorusmeState.serit = serit;
    renderGorusmeTrack();
    // Bir not hazır olduysa liste kendiliğinden tazelensin.
    if (oncekiHazir !== undefined && serit.hazir !== oncekiHazir) loadGorusme();
  } catch (err) {
    // Şerit ikincil bilgi; okunamazsa ekran yine çalışır.
  }
}

function startGorusmeTimer() {
  stopGorusmeTimer();
  const serit = gorusmeState.serit || {};
  const hizli = serit.kaydediliyor || serit.isleniyor > 0;
  gorusmeState.timer = setInterval(
    refreshGorusmeTrack,
    hizli ? GORUSME_TICK_MS : GORUSME_IDLE_MS
  );
}

function stopGorusmeTimer() {
  if (gorusmeState.timer) clearInterval(gorusmeState.timer);
  gorusmeState.timer = null;
}

// --- takip şeridi --------------------------------------------------------

function gorusmeSureText(saniye) {
  const toplam = Math.max(0, Number(saniye) || 0);
  const s = String(toplam % 60).padStart(2, "0");
  const d = String(Math.floor(toplam / 60) % 60).padStart(2, "0");
  const h = String(Math.floor(toplam / 3600)).padStart(2, "0");
  return `${h}:${d}:${s}`;
}

function renderGorusmeTrack() {
  const serit = gorusmeState.serit || {};
  const strip = el("gorusme-track");
  strip.classList.toggle("is-off", !serit.takip);

  el("gorusme-takip").checked = Boolean(serit.takip);
  el("gorusme-takip-text").textContent = serit.takip ? "Takip açık" : "Takip duraklatıldı";
  el("gorusme-takip-toggle").textContent = serit.takip ? "Duraklat" : "Sürdür";

  const live = el("gorusme-live");
  live.hidden = !serit.kaydediliyor;
  if (serit.kaydediliyor && serit.aktif) {
    live.textContent = "Görüşme algılandı · kaydediliyor " + gorusmeSureText(serit.aktif.sure_sn);
    const aygitlar = serit.aktif.aygitlar || {};
    el("gorusme-live-meta").textContent = [aygitlar.mikrofon_ad, aygitlar.hoparlor_ad]
      .filter(Boolean)
      .join(" + ");
  } else {
    el("gorusme-live-meta").textContent = "";
  }

  const working = el("gorusme-working");
  working.hidden = !serit.isleniyor;
  working.textContent = "işleniyor " + (serit.isleniyor || 0);

  const queued = el("gorusme-queued");
  queued.hidden = !serit.kuyrukta;
  queued.textContent = "kuyrukta " + (serit.kuyrukta || 0);

  el("gorusme-count").textContent = String(gorusmeState.notlar.length || serit.toplam || 0);
  el("gorusme-track-hint").textContent = gorusmeTrackHint();
  startGorusmeTimer();
}

function gorusmeTrackHint() {
  if (!gorusmeState.supported) return "Görüşme kaydı yalnız Windows'ta çalışır.";
  if (!gorusmeState.whisperVar) return "faster-whisper kurulu değil: notlar yazıya dökülemez.";
  const serit = gorusmeState.serit || {};
  if (!serit.takip) return "Duraklatılmışken hiçbir şey kaydedilmez.";
  return "";
}

async function toggleGorusmeTakip(acik) {
  try {
    gorusmeState.serit = await api("/api/gorusme/takip", {
      method: "POST",
      body: JSON.stringify({ acik: Boolean(acik) }),
    });
    renderGorusmeTrack();
    loadGorusme();
  } catch (err) {
    fail(err);
  }
}

// --- liste ---------------------------------------------------------------

const GORUSME_HEADS = ["Tarih", "Başlık", "Katılımcılar", "Süre", "Durum", "Görev", "Jira"];

function renderGorusmeList() {
  const head = el("gorusme-head");
  const body = el("gorusme-body");
  clear(head);
  clear(body);
  GORUSME_HEADS.forEach((text) => head.appendChild(h("th", { text })));

  const bos = gorusmeState.notlar.length === 0;
  el("gorusme-empty").hidden = !bos;
  el("gorusme-table").hidden = bos;
  el("gorusme-empty-text").textContent = callsState.query
    ? "Bu aramaya uyan görüşme notu yok."
    : "Henüz görüşme notu yok. Takip açıkken bir Teams görüşmesi başlatın.";

  gorusmeState.notlar.forEach((satir) => body.appendChild(gorusmeRow(satir)));
}

function gorusmeRow(satir) {
  const row = h("tr", { class: "gorusme-row", onclick: () => openGorusme(satir.id) });
  row.appendChild(h("td", { class: "when", text: stamp(satir.baslangic) }));
  row.appendChild(h("td", { class: "what", text: satir.baslik }));

  const kisiler = h("td", {}, []);
  if (satir.katilimcilar.length) {
    const kutu = h("div", { class: "who-chips" }, []);
    satir.katilimcilar.forEach((ad) => kutu.appendChild(h("span", { text: ad })));
    kisiler.appendChild(kutu);
  } else {
    kisiler.appendChild(h("span", { class: "hint", text: satir.katilimci_notu || "—" }));
  }
  row.appendChild(kisiler);

  row.appendChild(h("td", { class: "much", text: satir.sure_text }));
  row.appendChild(h("td", {}, gorusmeStateCell(satir)));
  row.appendChild(h("td", { class: "much", text: satir.gorev_sayisi ? String(satir.gorev_sayisi) : "–" }));
  row.appendChild(h("td", { class: "jira-key", text: satir.jira_key || "–" }));
  return row;
}

function gorusmeStateCell(satir) {
  const nodes = [
    h("span", {
      class: "pill state-" + satir.durum,
      text: satir.durum_label,
      title: satir.hata || "",
    }),
  ];
  // Yazıya dökme uzun sürüyor: satırda belirsiz bir ilerleme çubuğu durur.
  if (satir.isleniyor) {
    nodes.push(h("span", { class: "bar" }, [h("i", {})]));
  }
  return nodes;
}

// --- çekmece -------------------------------------------------------------

function openGorusmeDrawer(title, durum) {
  el("gorusme-drawer-title").textContent = title;
  const badge = el("gorusme-drawer-state");
  badge.textContent = durum || "";
  badge.hidden = !durum;
  el("gorusme-drawer").hidden = false;
  return el("gorusme-drawer-body");
}

function closeGorusmeDrawer() {
  el("gorusme-drawer").hidden = true;
  gorusmeState.drawerId = null;
}

async function openGorusme(notId) {
  gorusmeState.drawerId = notId;
  const body = openGorusmeDrawer("Görüşme notu", "");
  clear(body);
  body.appendChild(h("p", { class: "hint", text: "Okunuyor..." }));
  try {
    const data = await api("/api/gorusme/" + notId);
    if (gorusmeState.drawerId !== notId) return;
    renderGorusmeDetail(body, data);
  } catch (err) {
    clear(body);
    body.appendChild(h("p", { class: "hint", text: err.message }));
  }
}

function renderGorusmeDetail(body, data) {
  const not = data.not;
  clear(body);
  el("gorusme-drawer-title").textContent = not.baslik;
  const badge = el("gorusme-drawer-state");
  badge.textContent = not.durum_label;
  badge.hidden = false;

  body.appendChild(gorusmeActions(data));
  if (not.hata) {
    body.appendChild(h("p", { class: "warn-note", text: "Hata: " + not.hata }));
  }

  gorusmeSection(body, data, "ozet");
  gorusmeSection(body, data, "karar");
  gorusmeActionSection(body, data);
  gorusmeSection(body, data, "soru");

  // Sağ sütunun içeriği çekmecede alta iner: görüşme bilgisi, Jira, görevler.
  body.appendChild(h("h3", { text: "Görüşme" }));
  [
    ["Tarih", stamp(not.baslangic)],
    ["Süre", not.sure_text],
    ["Tür", not.tur_label || "—"],
    ["Kişiler", (not.katilimcilar || []).join(", ") || not.katilimci_notu || "—"],
  ].forEach(([label, value]) =>
    body.appendChild(
      h("div", { class: "detail-row" }, [
        h("div", { class: "label", text: label }),
        h("div", { class: "value", text: value }),
      ])
    )
  );

  body.appendChild(h("h3", { text: "Jira kaydı" }));
  body.appendChild(gorusmeJiraBox(data));

  body.appendChild(h("h3", { text: `Görevler (${(data.gorevler || []).length})` }));
  if ((data.gorevler || []).length) {
    const liste = h("div", { class: "call-list" }, []);
    data.gorevler.forEach((gorev) =>
      liste.appendChild(
        h("div", { class: "call-line" }, [
          h("span", { class: "what", text: gorev.title }),
          h("span", { class: "much", text: gorev.due_date || "" }),
        ])
      )
    );
    body.appendChild(liste);
  } else {
    body.appendChild(h("p", { class: "hint", text: "Bu nottan henüz görev üretilmedi." }));
  }

  body.appendChild(h("h3", { text: "Kaynaklar" }));
  body.appendChild(h("p", { class: "hint", text: data.kaynaklar }));
}

function gorusmeSection(body, data, tur) {
  const satirlar = (data.bolumler || {})[tur] || [];
  if (!satirlar.length) return;
  body.appendChild(h("h3", { text: data.bolum_basliklari[tur] }));
  const liste = h("ul", { class: "note-list" }, []);
  satirlar.forEach((satir) => liste.appendChild(h("li", { text: satir.metin })));
  body.appendChild(liste);
}

/** Aksiyonlar: her satırda "Görev yap" düğmesi. */
function gorusmeActionSection(body, data) {
  const satirlar = (data.bolumler || {}).aksiyon || [];
  if (!satirlar.length) return;
  body.appendChild(h("h3", { text: data.bolum_basliklari.aksiyon }));
  satirlar.forEach((satir) => {
    const kuyruk = [satir.kisi, satir.son_tarih].filter(Boolean).join(" · ");
    body.appendChild(
      h("div", { class: "action-row" }, [
        h("div", {}, [
          h("div", { text: satir.metin }),
          h("small", { class: "hint", text: kuyruk }),
        ]),
        h("button", {
          class: "small primary",
          text: "Görev yap",
          onclick: () => makeGorusmeTask(data.not.id, satir.id),
        }),
      ])
    );
  });
}

function gorusmeActions(data) {
  const not = data.not;
  const kutu = h("div", { class: "drawer-actions" }, []);
  if (data.transkript_var) {
    kutu.appendChild(
      h("button", {
        class: "small",
        text: "Yeniden özetle",
        onclick: () => resummarizeGorusme(not.id),
      })
    );
  }
  if (not.durum === "hata") {
    kutu.appendChild(
      h("button", {
        class: "small primary",
        text: "Yeniden dene",
        onclick: () => retryGorusme(not.id),
      })
    );
  }
  kutu.appendChild(
    h("button", { class: "small", text: "E-posta ile gönder", onclick: () => mailGorusme(not) })
  );
  kutu.appendChild(
    h("button", { class: "small danger", text: "Sil", onclick: () => deleteGorusme(not.id) })
  );
  return kutu;
}

/** Jira bağı: kayıt anahtarını yazarak ya da listeden arayarak bağla. */
function gorusmeJiraBox(data) {
  const kutu = h("div", { class: "jira-box" }, []);
  const giris = h("input", {
    type: "search",
    id: "gorusme-jira-input",
    placeholder: "PRJ-1234",
    value: data.not.jira_key || "",
  });
  kutu.appendChild(giris);
  kutu.appendChild(
    h("button", {
      class: "small primary",
      text: data.not.jira_key ? "Değiştir" : "Bağla",
      onclick: () => bindGorusmeJira(data.not.id, giris.value),
    })
  );
  if (data.not.jira_key) {
    kutu.appendChild(
      h("button", {
        class: "small",
        text: "Kaldır",
        onclick: () => bindGorusmeJira(data.not.id, ""),
      })
    );
  }
  return kutu;
}

// --- eylemler ------------------------------------------------------------

async function makeGorusmeTask(notId, bolumId) {
  try {
    const sonuc = await api(`/api/gorusme/${notId}/gorev`, {
      method: "POST",
      body: JSON.stringify({ bolum_id: bolumId }),
    });
    toast("Görev üretildi", sonuc.gorev.title, "ok");
    if (typeof refreshTaskBadge === "function") refreshTaskBadge();
    openGorusme(notId);
    loadGorusme();
  } catch (err) {
    fail(err);
  }
}

async function bindGorusmeJira(notId, key) {
  try {
    await api(`/api/gorusme/${notId}/jira`, { method: "PUT", body: JSON.stringify({ jira_key: key }) });
    openGorusme(notId);
    loadGorusme();
  } catch (err) {
    fail(err);
  }
}

async function retryGorusme(notId) {
  try {
    await api(`/api/gorusme/${notId}/yeniden-dene`, { method: "POST" });
    toast("Kuyruğa alındı", "Not yeniden işlenecek.", "ok");
    loadGorusme();
  } catch (err) {
    fail(err);
  }
}

async function resummarizeGorusme(notId) {
  try {
    await api(`/api/gorusme/${notId}/yeniden-ozetle`, { method: "POST" });
    toast("Yeniden özetlendi", "Not güncellendi.", "ok");
    openGorusme(notId);
    loadGorusme();
  } catch (err) {
    fail(err);
  }
}

async function deleteGorusme(notId) {
  if (!window.confirm("Görüşme notu silinsin mi?")) return;
  try {
    await api("/api/gorusme/" + notId, { method: "DELETE" });
    closeGorusmeDrawer();
    loadGorusme();
  } catch (err) {
    fail(err);
  }
}

async function mailGorusme(not) {
  const to = window.prompt("Kime gönderilsin? (e-posta adresi)", "");
  if (!to) return;
  try {
    const sonuc = await api(`/api/gorusme/${not.id}/eposta`, { method: "POST", body: JSON.stringify({ to }) });
    toast("E-posta", sonuc.displayed ? "Outlook'ta açıldı." : "Gönderildi.", "ok");
  } catch (err) {
    fail(err);
  }
}

// --- diğer ekranlara bağlanan parçalar ------------------------------------

/** Jira kayıt detayı: "Görüşme notları (n)" bölümü. */
async function renderDrawerGorusme(parent, key) {
  try {
    const data = await api("/api/gorusme/kayit/" + encodeURIComponent(key));
    if (!data.count) return;
    parent.appendChild(h("h3", { text: `Görüşme notları (${data.count})` }));
    parent.appendChild(gorusmeMiniList(data.notlar));
  } catch (err) {
    // Kayıt detayı görüşme notu olmadan da açılmalı.
  }
}

/** Kişiler çekmecesi: o kişiyle yapılan görüşmelerin notları. */
async function renderKisiGorusme(parent, counterpartId) {
  try {
    const data = await api("/api/gorusme/kisi/" + encodeURIComponent(counterpartId));
    if (!data.count) return;
    parent.appendChild(h("h3", { text: `Görüşme notları (${data.count})` }));
    parent.appendChild(gorusmeMiniList(data.notlar));
  } catch (err) {
    // Kişi çekmecesi not olmadan da açılmalı.
  }
}

function gorusmeMiniList(notlar) {
  const liste = h("div", { class: "call-list" }, []);
  notlar.forEach((satir) =>
    liste.appendChild(
      h("div", { class: "call-line", onclick: () => openGorusme(satir.id) }, [
        h("span", { class: "when", text: stamp(satir.baslangic) }),
        h("span", { class: "what", text: satir.baslik }),
        h("span", { class: "much", text: satir.durum_label }),
      ])
    )
  );
  return liste;
}

/** Aramalar sekmesi "Görüşme notları"na geçtiğinde çağrılır. */
function showGorusmeTab(acik) {
  el("gorusme-wrap").hidden = !acik;
  if (acik) loadGorusme();
}

// --- bağlama -------------------------------------------------------------

function bindGorusme() {
  el("gorusme-takip").addEventListener("change", (event) =>
    toggleGorusmeTakip(event.target.checked)
  );
  el("gorusme-takip-toggle").addEventListener("click", () =>
    toggleGorusmeTakip(!(gorusmeState.serit || {}).takip)
  );
  el("gorusme-drawer-close").addEventListener("click", closeGorusmeDrawer);
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !el("gorusme-drawer").hidden) closeGorusmeDrawer();
  });
  refreshGorusmeTrack();
}

document.addEventListener("DOMContentLoaded", bindGorusme);
