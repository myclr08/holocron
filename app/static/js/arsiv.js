// Arşiv: Holocron'un bilgi arşivi. Göreve ve Jira kaydına iliştirilen belgeler
// ("veri kartları"). Ortak yardımcılar (api, h, el, clear, toast, openModal,
// closeModal, fail) app.js ve common.js içinden gelir. Çerçeve yok, CDN yok.
//
// Dosyalar Belgeler\holocron\holocron-belgeler klasörüne KOPYALANIR; bağlar
// yalnızca bu makinede durur, Jira'ya hiç gitmez. Bağı kaldırmak dosyayı
// silmez; silme ayrı ve onaylı bir iştir ("Arşivden sil").

const ARSIV_FILTER_KEY = "holocron.arsiv.filtre";

const ARSIV_KINDS = [
  { id: "", label: "Tümü" },
  { id: "pdf", label: "PDF" },
  { id: "doc", label: "Belge" },
  { id: "xls", label: "Tablo" },
  { id: "img", label: "Görsel" },
  { id: "other", label: "Diğer" },
];

const ARSIV_STATES = [
  { id: "", label: "Hepsi" },
  { id: "linked", label: "Bağlı" },
  { id: "unlinked", label: "Bağlanmamış" },
];

const arsivState = {
  data: null,
  q: "",
  kind: "",
  state: "",
  request: 0,
  searchTimer: null,
};

// Detay ekranları her çizimde bölümü yeniden kurar; son liste önbellekten
// hemen çizilir, sonra sunucudan tazelenir (titreme olmasın).
const veriKartiOnbellek = new Map();

// --- biçim ---------------------------------------------------------------

function arsivBoyut(bytes) {
  const value = Number(bytes || 0);
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(value < 10240 ? 1 : 0)} KB`.replace(".", ",");
  return `${(value / (1024 * 1024)).toFixed(1)} MB`.replace(".", ",");
}

function arsivTarih(iso) {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  const pad = (n) => String(n).padStart(2, "0");
  return `${pad(date.getDate())}.${pad(date.getMonth() + 1)}.${date.getFullYear()}`;
}

// Tür simgesi: köşesi kıvrık veri kartı + türün işareti. Renk türe göre
// (CSS: .vk-pdf, .vk-doc ...). Kendi çizimimiz, 24x28 kutu, çizgi tabanlı.
const ARSIV_GLYPHS = {
  pdf: [["path", { d: "M7.5 14h9M7.5 17h9M7.5 20h5.5" }], ["rect", { x: "6.5", y: "21.6", width: "11", height: "0.01" }]],
  doc: [["path", { d: "M7.5 12.5h9M7.5 15.5h9M7.5 18.5h9M7.5 21.5h5" }]],
  xls: [["rect", { x: "7", y: "12", width: "10", height: "10", rx: "1" }], ["path", { d: "M7 15.4h10M7 18.6h10M10.4 12v10M13.7 12v10" }]],
  img: [["path", { d: "m6.8 21.4 3.6-4.4 2.4 2.7 1.6-1.8 3 3.5" }], ["circle", { cx: "14.6", cy: "13.6", r: "1.5" }]],
  other: [["circle", { cx: "8.6", cy: "17", r: "0.9" }], ["circle", { cx: "12", cy: "17", r: "0.9" }], ["circle", { cx: "15.4", cy: "17", r: "0.9" }]],
};

function arsivSimge(kind) {
  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("viewBox", "0 0 24 28");
  svg.setAttribute("class", "vk-icon vk-" + (ARSIV_GLYPHS[kind] ? kind : "other"));
  svg.setAttribute("aria-hidden", "true");
  const shapes = [
    ["path", { d: "M5 2.5h9.5L19 7v18.5H5z" }],
    ["path", { d: "M14.5 2.5V7H19" }],
  ].concat(ARSIV_GLYPHS[kind] || ARSIV_GLYPHS.other);
  shapes.forEach(([tag, attrs]) => {
    const shape = document.createElementNS(ns, tag);
    Object.entries(attrs).forEach(([key, value]) => shape.setAttribute(key, value));
    svg.appendChild(shape);
  });
  return svg;
}

// --- yükleme ---------------------------------------------------------------

/** Dosyaları sırayla yükler; hedef verilirse bağ da kurulur. */
async function arsivYukle(files, target) {
  const list = Array.from(files || []).filter((file) => file && file.size !== undefined);
  if (!list.length) return [];
  const done = [];
  let duplicates = 0;
  for (let index = 0; index < list.length; index += 1) {
    const file = list[index];
    if (list.length > 1) toast("Arşive alınıyor", `${index + 1}/${list.length} · ${file.name}`);
    const params = new URLSearchParams();
    if (target && target.type === "task") params.set("task", String(target.id));
    if (target && target.type === "issue") params.set("issue", String(target.id));
    const query = params.toString();
    try {
      const data = await api(`/api/arsiv/upload${query ? "?" + query : ""}`, {
        method: "POST",
        body: file,
        headers: {
          "Content-Type": "application/octet-stream",
          "X-File-Name": encodeURIComponent(file.name),
        },
      });
      if (data.duplicate) duplicates += 1;
      done.push(data.document);
    } catch (err) {
      toast("Arşive alınamadı", `${file.name}: ${err.message}`, "error");
    }
  }
  if (done.length) {
    const note = duplicates
      ? `${duplicates} belge zaten Arşiv'deydi; ikinci kopya yazılmadı, bağ kuruldu.`
      : "Dosyalar Arşiv klasörüne kopyalandı.";
    toast(done.length === 1 ? "Veri kartı eklendi" : `${done.length} veri kartı eklendi`, note, "ok");
  }
  refreshArsivCount();
  return done;
}

/** Sürükle-bırak + dosya seçici. `onFiles(fileList)` çağrılır. */
function arsivDropAlani(onFiles, text) {
  const input = h("input", { type: "file", multiple: true, hidden: true });
  input.addEventListener("change", () => {
    if (input.files && input.files.length) onFiles(input.files);
    input.value = "";
  });
  const pick = h("button", {
    type: "button",
    class: "small",
    text: "Dosya seç",
    onclick: (event) => {
      event.preventDefault();
      input.click();
    },
  });
  const zone = h("div", { class: "vk-drop", tabindex: "0" }, [
    h("span", { class: "vk-drop-text", text: text || "Dosyayı buraya sürükleyin" }),
    pick,
    input,
  ]);
  bindArsivDrop(zone, onFiles);
  return zone;
}

function bindArsivDrop(node, onFiles) {
  let depth = 0;
  const hasFiles = (event) =>
    event.dataTransfer && Array.from(event.dataTransfer.types || []).includes("Files");
  node.addEventListener("dragenter", (event) => {
    if (!hasFiles(event)) return;
    event.preventDefault();
    depth += 1;
    node.classList.add("vk-over");
  });
  node.addEventListener("dragover", (event) => {
    if (!hasFiles(event)) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = "copy";
  });
  node.addEventListener("dragleave", () => {
    depth = Math.max(0, depth - 1);
    if (!depth) node.classList.remove("vk-over");
  });
  node.addEventListener("drop", (event) => {
    if (!hasFiles(event)) return;
    event.preventDefault();
    event.stopPropagation();
    depth = 0;
    node.classList.remove("vk-over");
    onFiles(event.dataTransfer.files);
  });
}

// --- ortak eylemler ------------------------------------------------------

async function arsivAc(doc) {
  try {
    await api(`/api/arsiv/${doc.id}/open`, { method: "POST" });
  } catch (err) {
    fail(err);
  }
}

async function arsivKlasordeGoster(doc) {
  try {
    await api(`/api/arsiv/${doc.id}/reveal`, { method: "POST" });
  } catch (err) {
    fail(err);
  }
}

async function arsivBagKaldir(doc, link, after) {
  try {
    await api(
      `/api/arsiv/${doc.id}/links/${encodeURIComponent(link.type)}/${encodeURIComponent(link.target)}`,
      { method: "DELETE" }
    );
    toast("Bağ kaldırıldı", `${doc.name} Arşiv'de kalır.`, "ok");
    refreshArsivCount();
    if (after) after();
  } catch (err) {
    fail(err);
  }
}

/** "Arşivden sil": Kamino gibi, kayıtlardan iz bırakmadan. Onaylı. */
function arsivdenSil(doc, after) {
  const count = (doc.links || []).length;
  const body = h("div", { class: "vk-confirm" }, [
    h("p", {}, [h("strong", { text: doc.name })]),
    h("p", {
      text:
        "Kamino gibi olacak: bu veri kartı arşivden silinir, klasördeki dosya da gider. " +
        "Sonradan arayan, kayıtlarda tek iz bulamaz.",
    }),
    h("p", {
      class: "hint",
      text: count
        ? `${count} bağ da kaldırılır. Geri alınamaz.`
        : "Hiçbir göreve ya da kayda bağlı değil. Geri alınamaz.",
    }),
  ]);
  openModal("Arşivden sil", body, [
    { label: "Vazgeç", onClick: closeModal },
    {
      label: "Arşivden sil",
      kind: "danger",
      onClick: async () => {
        try {
          await api(`/api/arsiv/${doc.id}`, { method: "DELETE" });
          closeModal();
          toast("Arşivden silindi", doc.name, "ok");
          refreshArsivCount();
          if (after) after();
        } catch (err) {
          fail(err);
        }
      },
    },
  ]);
}

function arsivBagEtiketi(link) {
  if (link.type === "issue") return link.label ? `${link.target} · ${link.label}` : link.target;
  return link.label || `Görev #${link.target}`;
}

// --- Arşiv ekranı ------------------------------------------------------------

function showArsiv() {
  if (typeof leaveTasks === "function") leaveTasks();
  if (typeof leaveCampaign === "function") leaveCampaign();
  if (typeof leaveAmbar === "function") leaveAmbar();
  state.view = "arsiv";
  el("placeholder").hidden = true;
  el("group-view").hidden = true;
  el("arsiv-view").hidden = false;
  el("arsiv-entry").classList.add("active");
  if (typeof renderGroups === "function") renderGroups();
  if (typeof closeDrawer === "function") closeDrawer();
  if (typeof saveView === "function") saveView();
  loadArsiv(true);
}

function leaveArsiv() {
  el("arsiv-view").hidden = true;
  el("arsiv-entry").classList.remove("active");
  if (state.view === "arsiv") state.view = "groups";
}

async function refreshArsivCount() {
  try {
    const data = await api("/api/arsiv/count");
    el("arsiv-count").textContent = String(data.total || 0);
    el("arsiv-entry").title = data.unlinked
      ? `Arşiv: ${data.total} belge, ${data.unlinked} bağlanmamış`
      : "Arşiv: göreve ve kayda iliştirilen belgeler";
  } catch (err) {
    // Sayı ikincil bilgi; okunamazsa kenar çubuğu yine çalışır.
  }
}

async function loadArsiv(scan) {
  const token = ++arsivState.request;
  const params = new URLSearchParams();
  if (arsivState.q) params.set("q", arsivState.q);
  if (arsivState.kind) params.set("kind", arsivState.kind);
  if (arsivState.state) params.set("state", arsivState.state);
  params.set("scan", scan ? "1" : "0");
  try {
    const data = await api(`/api/arsiv?${params.toString()}`);
    if (token !== arsivState.request) return;
    arsivState.data = data;
    renderArsiv();
    el("arsiv-count").textContent = String(data.counts.total || 0);
  } catch (err) {
    if (token !== arsivState.request) return;
    fail(err);
  }
}

function arsivPicker(box, options, current, onPick) {
  clear(box);
  options.forEach((option) => {
    box.appendChild(
      h("button", {
        type: "button",
        class: option.id === current ? "on" : "",
        text: option.label,
        "aria-pressed": String(option.id === current),
        onclick: () => onPick(option.id),
      })
    );
  });
}

function saveArsivFilter() {
  try {
    localStorage.setItem(ARSIV_FILTER_KEY, JSON.stringify({ kind: arsivState.kind, state: arsivState.state }));
  } catch (err) {
    // Depolama kapalı: süzgeç yalnızca bu oturumda hatırlanır.
  }
}

function renderArsiv() {
  const data = arsivState.data;
  if (!data) return;
  arsivPicker(el("arsiv-kinds"), ARSIV_KINDS, arsivState.kind, (id) => {
    arsivState.kind = id;
    saveArsivFilter();
    loadArsiv(false);
  });
  arsivPicker(el("arsiv-states"), ARSIV_STATES, arsivState.state, (id) => {
    arsivState.state = id;
    saveArsivFilter();
    loadArsiv(false);
  });
  const counts = data.counts || {};
  el("arsiv-total").textContent = `${counts.total || 0} veri kartı`;
  el("arsiv-folder").textContent = data.folder || "";
  el("arsiv-hint").textContent = counts.unlinked ? `${counts.unlinked} bağlanmamış` : "";

  const grid = el("arsiv-grid");
  clear(grid);
  const docs = data.documents || [];
  const filtered = !!(arsivState.q || arsivState.kind || arsivState.state);
  el("arsiv-empty").hidden = !!docs.length || filtered;
  el("arsiv-none").hidden = !!docs.length || !filtered;
  docs.forEach((doc) => grid.appendChild(arsivKart(doc)));
}

function arsivKart(doc) {
  const links = doc.links || [];
  const chips = h("div", { class: "vk-links" }, []);
  if (!links.length) {
    chips.appendChild(h("span", { class: "vk-tag", text: "bağlanmamış" }));
  }
  links.forEach((link) => {
    chips.appendChild(
      h("span", { class: "vk-chip vk-chip-" + link.type, title: arsivBagEtiketi(link) }, [
        icon(link.type === "issue" ? "issue" : "task"),
        h("span", { class: "vk-chip-text", text: arsivBagEtiketi(link) }),
        h("button", {
          type: "button",
          title: "Bağı kaldır (dosya Arşiv'de kalır)",
          "aria-label": "Bağı kaldır",
          text: "✕",
          onclick: () => arsivBagKaldir(doc, link, () => loadArsiv(false)),
        }),
      ])
    );
  });

  const meta = [arsivBoyut(doc.size), arsivTarih(doc.created_at), doc.kind_label].filter(Boolean);
  const card = h("article", { class: "veri-karti" + (doc.missing ? " is-missing" : "") }, [
    h("div", { class: "vk-head" }, [
      arsivSimge(doc.kind),
      h("div", { class: "vk-title" }, [
        h("button", {
          type: "button",
          class: "vk-name",
          title: doc.missing ? "Dosya klasörde yok" : `${doc.name} · varsayılan uygulamada aç`,
          text: doc.name,
          disabled: doc.missing,
          onclick: () => arsivAc(doc),
        }),
        h("div", { class: "vk-meta", text: doc.missing ? "dosya kayıp · " + meta.join(" · ") : meta.join(" · ") }),
      ]),
    ]),
    chips,
    h("div", { class: "vk-actions" }, [
      h("button", { type: "button", class: "small", text: "Aç", disabled: doc.missing, onclick: () => arsivAc(doc) }),
      h("button", {
        type: "button",
        class: "small",
        text: "Klasörde göster",
        disabled: doc.missing,
        onclick: () => arsivKlasordeGoster(doc),
      }),
      h("button", { type: "button", class: "small", text: "Bağla", onclick: () => arsivBaglaModal(doc) }),
      h("button", {
        type: "button",
        class: "small danger",
        text: "Arşivden sil",
        onclick: () => arsivdenSil(doc, () => loadArsiv(false)),
      }),
    ]),
  ]);
  return card;
}

/** Arşiv ekranından bağ: açık bir göreve ya da bir Jira anahtarına. */
async function arsivBaglaModal(doc) {
  const taskSelect = h("select", {}, [h("option", { value: "", text: "Görev seçin" })]);
  try {
    const board = await api("/api/tasks");
    (board.columns || []).forEach((column) => {
      const group = h("optgroup", { label: column.label || column.id }, []);
      (column.tasks || []).forEach((task) =>
        group.appendChild(h("option", { value: String(task.id), text: task.title }))
      );
      if (group.childNodes.length) taskSelect.appendChild(group);
    });
  } catch (err) {
    // Pano okunamazsa yalnızca Jira anahtarı ile bağ kurulur.
  }
  const keyInput = h("input", { type: "text", placeholder: "DEMO-1", autocomplete: "off" });
  const body = h("div", {}, [
    h("p", { class: "hint", text: `${doc.name} için yeni bir bağ. Bağ yalnızca bu makinede durur.` }),
    field("Görev", taskSelect),
    field("ya da Jira kaydı", keyInput),
  ]);
  const save = async () => {
    const payload = taskSelect.value
      ? { type: "task", target: taskSelect.value }
      : { type: "issue", target: keyInput.value.trim() };
    if (!payload.target) {
      toast("Bağ kurulmadı", "Bir görev seçin ya da Jira anahtarı yazın.", "error");
      return;
    }
    try {
      await api(`/api/arsiv/${doc.id}/links`, { method: "POST", body: JSON.stringify(payload) });
      closeModal();
      toast("Bağ kuruldu", doc.name, "ok");
      refreshArsivCount();
      loadArsiv(false);
    } catch (err) {
      fail(err);
    }
  };
  openModal("Veri kartını bağla", body, [
    { label: "Vazgeç", onClick: closeModal },
    { label: "Bağla", kind: "primary", onClick: save },
  ]);
}

// --- detay ekranlarındaki "Veri kartları" bölümü ---------------------------

/**
 * Görev penceresinde ve kayıt çekmecesinde aynı bölüm. `type`: "task" ya da
 * "issue"; `id`: görev kimliği ya da Jira anahtarı.
 */
function veriKartlariBolumu(type, id) {
  const cacheKey = `${type}:${id}`;
  const list = h("div", { class: "vk-mini-list" }, []);
  const count = h("span", { class: "badge", text: "" });
  const section = h("section", { class: "veri-kartlari" }, [
    h("div", { class: "vk-section-head" }, [h("h3", { text: "Veri kartları" }), count]),
    list,
  ]);

  const paint = (docs) => {
    clear(list);
    count.textContent = String(docs.length);
    count.hidden = !docs.length;
    if (!docs.length) {
      list.appendChild(
        h("p", { class: "hint", text: "Bağlı belge yok. Dosyalar Arşiv'e kopyalanır, Jira'ya gitmez." })
      );
    }
    docs.forEach((doc) => list.appendChild(veriKartiMini(doc, type, id, reload)));
  };

  const reload = async () => {
    try {
      const data = await api(`/api/arsiv/for/${encodeURIComponent(type)}/${encodeURIComponent(id)}`);
      veriKartiOnbellek.set(cacheKey, data.documents || []);
      if (section.isConnected || !list.childNodes.length) paint(data.documents || []);
    } catch (err) {
      if (!list.childNodes.length) {
        clear(list);
        list.appendChild(h("p", { class: "hint", text: "Veri kartları okunamadı: " + err.message }));
      }
    }
  };

  const onFiles = async (files) => {
    await arsivYukle(files, { type, id });
    reload();
  };

  section.appendChild(
    h("div", { class: "vk-section-tools" }, [
      arsivDropAlani(onFiles, "Dosyayı buraya sürükleyin"),
      h("button", {
        type: "button",
        class: "small",
        text: "Arşivden bağla",
        title: "Arşiv'de duran bir belgeyi bağla",
        onclick: (event) => {
          event.preventDefault();
          arsivdenBagla(type, id, reload);
        },
      }),
    ])
  );
  // Bölümün tamamı da bırakma alanıdır: küçük kutuyu tutturmak gerekmesin.
  bindArsivDrop(section, onFiles);

  if (veriKartiOnbellek.has(cacheKey)) paint(veriKartiOnbellek.get(cacheKey));
  reload();
  return section;
}

function veriKartiMini(doc, type, id, after) {
  return h("div", { class: "vk-mini" + (doc.missing ? " is-missing" : "") }, [
    arsivSimge(doc.kind),
    h("button", {
      type: "button",
      class: "vk-name",
      text: doc.name,
      title: doc.missing ? "Dosya klasörde yok" : "Varsayılan uygulamada aç",
      disabled: doc.missing,
      onclick: (event) => {
        event.preventDefault();
        arsivAc(doc);
      },
    }),
    h("span", { class: "vk-meta", text: doc.missing ? "kayıp" : arsivBoyut(doc.size) }),
    h("button", {
      type: "button",
      class: "vk-unlink",
      title: "Bağı kaldır (dosya Arşiv'de kalır)",
      "aria-label": "Bağı kaldır",
      text: "✕",
      onclick: (event) => {
        event.preventDefault();
        arsivBagKaldir(doc, { type, target: String(id) }, after);
      },
    }),
  ]);
}

/** Arşiv'deki bir belgeyi bu göreve/kayda bağlar: arama kutulu küçük liste. */
async function arsivdenBagla(type, id, after) {
  const box = h("div", { class: "vk-pick" }, []);
  const search = h("input", { type: "search", placeholder: "Arşivde ara", autocomplete: "off" });
  const list = h("div", { class: "vk-pick-list" }, []);
  box.appendChild(search);
  box.appendChild(list);

  // Pencere görev penceresinin üstüne açılır; kapanınca görev penceresi
  // yeniden çizilmez, bu yüzden bağdan sonra bölüm tazelenir.
  const overlay = h("div", { class: "vk-pick-overlay" }, [
    h("div", { class: "vk-pick-box", role: "dialog", "aria-modal": "true" }, [
      h("header", { class: "vk-pick-head" }, [
        h("strong", { text: "Arşivden bağla" }),
        h("button", { type: "button", text: "✕", title: "Kapat", onclick: () => overlay.remove() }),
      ]),
      box,
    ]),
  ]);
  overlay.addEventListener("click", (event) => {
    if (event.target === overlay) overlay.remove();
  });
  overlay.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      event.stopPropagation();
      overlay.remove();
    }
  });
  document.body.appendChild(overlay);
  search.focus();

  let timer = null;
  const load = async () => {
    try {
      const data = await api(`/api/arsiv?scan=0&q=${encodeURIComponent(search.value.trim())}`);
      clear(list);
      const target = String(id).toUpperCase();
      const docs = (data.documents || []).filter(
        (doc) => !(doc.links || []).some((link) => link.type === type && String(link.target).toUpperCase() === target)
      );
      if (!docs.length) {
        list.appendChild(
          h("p", {
            class: "hint vk-quote",
            text: search.value.trim() ? "Arşivlerde yoksa, yok demektir." : "Bağlanacak başka belge yok.",
          })
        );
      }
      docs.forEach((doc) =>
        list.appendChild(
          h("button", {
            type: "button",
            class: "vk-pick-row",
            onclick: async () => {
              try {
                await api(`/api/arsiv/${doc.id}/links`, {
                  method: "POST",
                  body: JSON.stringify({ type, target: String(id) }),
                });
                overlay.remove();
                toast("Bağ kuruldu", doc.name, "ok");
                refreshArsivCount();
                if (after) after();
              } catch (err) {
                fail(err);
              }
            },
          }, [arsivSimge(doc.kind), h("span", { text: doc.name }), h("span", { class: "vk-meta", text: arsivBoyut(doc.size) })])
        )
      );
    } catch (err) {
      fail(err);
    }
  };
  search.addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(load, 200);
  });
  load();
}

// --- bağlama -------------------------------------------------------------------

document.addEventListener("DOMContentLoaded", () => {
  try {
    const saved = JSON.parse(localStorage.getItem(ARSIV_FILTER_KEY) || "{}");
    if (ARSIV_KINDS.some((item) => item.id === saved.kind)) arsivState.kind = saved.kind;
    if (ARSIV_STATES.some((item) => item.id === saved.state)) arsivState.state = saved.state;
  } catch (err) {
    // Depolama kapalı ya da bozuk: varsayılan süzgeç.
  }
  el("arsiv-entry").addEventListener("click", showArsiv);
  el("arsiv-entry").addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      showArsiv();
    }
  });
  el("arsiv-search").addEventListener("input", (event) => {
    arsivState.q = event.target.value.trim();
    clearTimeout(arsivState.searchTimer);
    arsivState.searchTimer = setTimeout(() => loadArsiv(false), 180);
  });
  el("arsiv-rescan").addEventListener("click", () => loadArsiv(true));
  el("arsiv-open-folder").addEventListener("click", async () => {
    try {
      await api("/api/arsiv/folder", { method: "POST" });
    } catch (err) {
      fail(err);
    }
  });
  const picker = el("arsiv-file");
  el("arsiv-add").addEventListener("click", () => picker.click());
  picker.addEventListener("change", async () => {
    if (picker.files && picker.files.length) {
      await arsivYukle(picker.files, null);
      loadArsiv(false);
    }
    picker.value = "";
  });
  // Arşiv ekranının tamamı bırakma alanı: bırakılan dosya bağlanmamış girer.
  bindArsivDrop(el("arsiv-view"), async (files) => {
    await arsivYukle(files, null);
    loadArsiv(false);
  });
  refreshArsivCount();
});
