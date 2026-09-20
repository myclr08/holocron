// Ana ekran: hangar (gruplar), grid, sutun secici, detay cekmecesi, Guncelle.
// Cerceve yok, CDN yok: her sey dogrudan DOM uzerinde kurulur.

const KIND_LABEL = { manual: "Manuel", filter: "JQL filtresi" };
const SABER_COLORS = ["blue", "green", "purple", "red", "yellow", "white"];
const POLL_MS = 700;
const CHANGED_MS = 5000;
const TOAST_MS = 6000;
// Is bitince halkanin yesil parladigi sure; sonra ozet balonu gelir.
const DONE_MS = 1500;
// Acilis animasyonu 9 sn; biraz pay birakilir.
// Acilis suresi: 24 sn akis + 1.5 sn bekleme + 0.6 sn sonme (app.css ile ayni).
const CRAWL_MS = 26100;
// Olum Yildizi halkasinin cevresi (r = 14.2, viewBox 32).
const RING_LENGTH = 2 * Math.PI * 14.2;

const state = {
  groups: [],
  activeId: null,
  group: null,
  columns: [],
  rows: [],
  fields: [],
  sort: null,
  query: "",
  baseUrl: "",
  shown: 0,
  total: 0,
  changed: {},
  changedTimer: null,
  searchTimer: null,
  pollTimer: null,
  drawerKey: null,
  drawerFields: [],
  drawerLocal: [],
  drawerFetchedAt: null,
  drawerShowEmpty: false,
  lastRefreshState: "idle",
  catalogEmpty: false,
  editing: null,
  popover: null,
  toastTimer: null,
  doneTimer: null,
  crawlTimer: null,
  view: "groups",
  teams: null,
  // Grid'deki onay kutusu sutunu; grup degisince sifirlanir.
  selectedKeys: new Set(),
  mailSupported: true,
  tasks: {
    columns: [],
    oldDone: 0,
    includeOld: false,
    query: "",
    summary: { open: 0, overdue: 0 },
    baseUrl: "",
    drag: null,
    mail: false,
  },
};

// --- kucuk yardimcilar --------------------------------------------------

const el = (id) => document.getElementById(id);

function h(tag, attrs, children) {
  const node = document.createElement(tag);
  Object.entries(attrs || {}).forEach(([name, value]) => {
    if (value === null || value === undefined || value === false) return;
    if (name === "class") node.className = value;
    else if (name === "text") node.textContent = value;
    else if (name.startsWith("on")) node.addEventListener(name.slice(2), value);
    else if (value === true) node.setAttribute(name, "");
    else node.setAttribute(name, value);
  });
  (children || []).forEach((child) => child && node.appendChild(child));
  return node;
}

function clear(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
}

// "local:3:changes" gibi kimlikler sinif adinda kullanilamaz; sadelestirilir.
function cssName(fieldId) {
  return String(fieldId).replace(/[^A-Za-z0-9_-]/g, "-");
}

function toast(title, message, kind, items) {
  const box = el("toast");
  clear(box);
  box.className = "toast" + (kind ? " " + kind : "");
  clearTimeout(state.toastTimer);
  state.toastTimer = setTimeout(() => {
    box.hidden = true;
  }, TOAST_MS);
  box.appendChild(
    h("div", { class: "toast-head" }, [
      h("strong", { text: title }),
      h("button", { text: "×", title: "Kapat", onclick: () => (box.hidden = true) }),
    ])
  );
  if (message) box.appendChild(h("div", { text: message }));
  if (items && items.length) {
    const list = h("ul", {}, []);
    const notes = [];
    items.forEach((item) => {
      // Alt metin madde isareti almaz; listenin altina duz satir olarak duser.
      if (item.classList && item.classList.contains("toast-sub")) notes.push(item);
      else list.appendChild(h("li", {}, [item]));
    });
    if (list.childNodes.length) box.appendChild(list);
    notes.forEach((note) => box.appendChild(note));
  }
  box.hidden = false;
}

function fail(err) {
  toast("İşlem yapılamadı", err.message || String(err), "error");
}

// --- son gorunum: hash + localStorage yedegi -----------------------------
// Ayarlar'dan "Geri" ile donuldugunde son bakilan yere dusmek icin ana
// ekranin gorunumu adres cubugunda ve localStorage'da tutulur.

const LAST_VIEW_KEY = "holocron.lastView";

/** Verilen goruntu/aktif-filo icin hash uretir; taniyamiyorsa bos doner. */
function viewHash(view, activeId) {
  if (view === "tasks") return "#gorevlerim";
  if (view === "campaign") return "#sefer";
  if (view === "groups" && activeId) return "#filo/" + encodeURIComponent(activeId);
  return "";
}

/** Su anki gorunumu hash'e ve localStorage'a yazar; ikisi de sessizce basarisiz olabilir. */
function saveView() {
  const hash = viewHash(state.view, state.activeId);
  if (!hash) return;
  try {
    if (window.location.hash !== hash) window.history.replaceState(null, "", hash);
  } catch (err) {
    // adres cubugu guncellenemedi (ozel sekme vb.); localStorage yedek kalir
  }
  try {
    localStorage.setItem(LAST_VIEW_KEY, hash);
  } catch (err) {
    // depolama kapali/dolu olabilir: hatirlamak zorunlu degil
  }
}

/** Acilista hangi gorunume gidilecegini cozer: once hash, sonra localStorage. */
function resolveStartupView() {
  let hash = "";
  try {
    hash = window.location.hash || "";
  } catch (err) {
    hash = "";
  }
  if (!hash) {
    try {
      hash = localStorage.getItem(LAST_VIEW_KEY) || "";
    } catch (err) {
      hash = "";
    }
  }
  if (hash === "#gorevlerim") return { view: "tasks", groupId: null };
  if (hash === "#sefer") return { view: "campaign", groupId: null };
  const match = /^#filo\/(.+)$/.exec(hash);
  if (match) {
    // Grup id'leri sayisal (SQLite rowid); hash'ten string gelir, karsilastirma icin cevrilir.
    const raw = decodeURIComponent(match[1]);
    const id = Number(raw);
    return { view: "groups", groupId: Number.isFinite(id) ? id : null };
  }
  return { view: "groups", groupId: null };
}

// --- modal --------------------------------------------------------------

function openModal(title, body, buttons, options) {
  el("modal-title").textContent = title;
  // Sutun secici gibi iki listeli pencereler genis kipte acilir.
  el("modal-box").classList.toggle("wide", !!(options && options.wide));
  const bodyBox = el("modal-body");
  clear(bodyBox);
  bodyBox.appendChild(body);

  const foot = el("modal-foot");
  clear(foot);
  (buttons || []).forEach((spec) =>
    foot.appendChild(h("button", { class: spec.kind || "", text: spec.label, onclick: spec.onClick }))
  );
  el("modal").hidden = false;
  const focusable = bodyBox.querySelector("input, textarea, select");
  if (focusable) focusable.focus();
}

function closeModal() {
  el("modal").hidden = true;
}

function field(labelText, control) {
  return h("div", { class: "field" }, [h("label", { text: labelText }), control]);
}

/** Alani "Düzelt" dugmesiyle sarmalar; bilesen yoksa alanin kendisi doner.
 *
 * Teams yapistirma cevirisi de burada takilir: "Düzelt" dugmesi tasiyan her
 * alana (gorev Aciklama/Son durum/Not ve cekmecedeki cok satirli yerel
 * alanlar) panodaki Teams mesaj blogu belirtec olarak duser.
 */
function duzeltKutusu(alan, ad) {
  if (typeof attachTeamsLink === "function") attachTeamsLink(alan);
  if (typeof attachDuzelt !== "function") return alan;
  return attachDuzelt(alan, { ad: ad }) || alan;
}

/** Salt-okunur metin: Teams belirteci cipe, baglantilar tiklanir hale gelir.
 *
 * Metin HTML olarak BASILMAZ: parcalar metin dugumu, baglantilar bizim
 * urettigimiz `a` elemanlaridir (bkz. teamslink.js).
 */
function metinCiz(dugum, metin) {
  if (typeof teamsLinkDoldur === "function") return teamsLinkDoldur(dugum, metin);
  dugum.textContent = metin === null || metin === undefined ? "" : String(metin);
  return dugum;
}

// --- gruplar ------------------------------------------------------------

async function loadGroups(selectId) {
  const data = await api("/api/groups");
  state.groups = data.groups;
  const wanted = selectId || state.activeId;
  if (state.groups.some((group) => group.id === wanted)) await selectGroup(wanted, true);
  else if (state.groups.length) await selectGroup(state.groups[0].id, true);
  else {
    showPlaceholder();
    renderGroups();
  }
}

function renderGroups() {
  const list = el("group-list");
  clear(list);
  if (!state.groups.length) {
    list.appendChild(h("div", { class: "empty", text: "Henüz grup yok." }));
    return;
  }
  state.groups.forEach((group, index) => {
    const row = h(
      "div",
      {
        class:
          "group-item" +
          (state.view === "groups" && group.id === state.activeId ? " active" : ""),
        title: group.kind === "filter" ? group.jql : "Manuel grup",
        onclick: () => selectGroup(group.id),
      },
      [
        h("span", { class: "color-strip color-" + group.color }),
        h("span", { class: "name", text: group.name }),
        h("span", { class: "count", text: String(group.count) }),
        h("span", {}, [
          h("button", {
            class: "move",
            text: "↑",
            title: "Yukarı taşı",
            disabled: index === 0,
            onclick: (event) => {
              event.stopPropagation();
              move(index, -1);
            },
          }),
          h("button", {
            class: "move",
            text: "↓",
            title: "Aşağı taşı",
            disabled: index === state.groups.length - 1,
            onclick: (event) => {
              event.stopPropagation();
              move(index, 1);
            },
          }),
        ]),
      ]
    );
    list.appendChild(row);
  });
}

async function move(index, delta) {
  const ids = state.groups.map((group) => group.id);
  const target = index + delta;
  if (target < 0 || target >= ids.length) return;
  const swap = ids[index];
  ids[index] = ids[target];
  ids[target] = swap;
  try {
    const data = await api("/api/groups/reorder", { method: "POST", body: JSON.stringify({ ids }) });
    state.groups = data.groups;
    renderGroups();
  } catch (err) {
    fail(err);
  }
}

function showPlaceholder() {
  if (state.view === "tasks" || state.view === "campaign") return;
  state.activeId = null;
  state.group = null;
  el("placeholder").hidden = false;
  el("group-view").hidden = true;
}

async function selectGroup(groupId, keepView) {
  const changing = state.activeId !== groupId;
  if (changing && state.drawerKey) closeDrawer();
  // Arka planda tazeleme (keepView) gorev panosunu kapatmaz; grubu tiklamak kapatir.
  if (!keepView) {
    leaveTasks();
    if (typeof leaveCampaign === "function") leaveCampaign();
  }
  state.activeId = groupId;
  if (changing || !keepView) {
    state.query = "";
    el("search").value = "";
    state.sort = null;
    clearSelection();
  }
  el("placeholder").hidden = true;
  if (state.view !== "tasks" && state.view !== "campaign") {
    el("group-view").hidden = false;
  }
  renderGroups();
  await loadIssues();
  saveView();
}

function renderGroupHead() {
  const group = state.group;
  if (!group) return;
  el("group-color").className = "color-strip color-" + group.color;
  el("group-name").textContent = group.name;
  el("group-kind").textContent = KIND_LABEL[group.kind] || group.kind;
  el("group-count").textContent = group.count + " kayıt";
  const jqlLine = el("group-jql");
  jqlLine.textContent = group.jql ? "JQL: " + group.jql : "";
  jqlLine.hidden = !group.jql;
}

// --- secim (onay kutusu sutunu) -----------------------------------------
//
// Secim grid'in ustunde durur: "E-posta ile gonder" ve Excel penceresindeki
// "yalniz secili" kutusu bunu okur. Kume grup degisince sifirlanir; suzgec
// degisince DURUR, kullanici arayip secip aramayi temizleyince secimini
// kaybetmesin.

function clearSelection() {
  state.selectedKeys = new Set();
  renderPickCount();
}

/** Secili anahtarlar; sira grid'deki sira degil, secim sirasidir. */
function selectedKeyList() {
  return Array.from(state.selectedKeys);
}

function toggleKey(key, on) {
  if (on) state.selectedKeys.add(key);
  else state.selectedKeys.delete(key);
  renderPickCount();
}

/** Rozet ve "tumunu sec" kutusunun uc durumu (bos / karisik / dolu). */
function renderPickCount() {
  const badge = el("pick-count");
  if (!badge) return;
  const count = state.selectedKeys.size;
  badge.textContent = count ? count + " seçili" : "";
  badge.hidden = count === 0;
  const all = el("pick-all");
  if (all) {
    const shown = state.rows.length;
    const picked = state.rows.filter((row) => state.selectedKeys.has(row.key)).length;
    all.checked = shown > 0 && picked === shown;
    all.indeterminate = picked > 0 && picked < shown;
  }
}

/** Basliktaki kutu yalnizca GORUNEN (suzulmus) satirlari kapsar. */
function toggleAllVisible(on) {
  state.rows.forEach((row) => {
    if (on) state.selectedKeys.add(row.key);
    else state.selectedKeys.delete(row.key);
  });
  renderGrid();
}

// --- grid ---------------------------------------------------------------

async function loadIssues() {
  if (!state.activeId) return;
  const params = new URLSearchParams();
  if (state.query) params.set("q", state.query);
  if (state.sort) {
    params.set("sort", state.sort.field);
    params.set("dir", state.sort.dir);
  }
  const query = params.toString();
  try {
    const data = await api(`/api/groups/${state.activeId}/issues${query ? "?" + query : ""}`);
    state.group = data.group;
    state.columns = data.columns;
    state.rows = data.rows;
    state.sort = data.sort;
    state.baseUrl = data.base_url;
    state.shown = data.shown;
    state.total = data.total;
    state.catalogEmpty = !!data.catalog_empty;
    const index = state.groups.findIndex((group) => group.id === data.group.id);
    if (index >= 0) state.groups[index] = data.group;
    renderGroups();
    renderGroupHead();
    renderGrid();
  } catch (err) {
    fail(err);
  }
}

function renderGrid() {
  const head = el("grid-head");
  clear(head);
  const all = h("input", {
    type: "checkbox",
    id: "pick-all",
    title: "Görünen satırların tümünü seç",
    onclick: (event) => {
      event.stopPropagation();
      toggleAllVisible(event.target.checked);
    },
  });
  head.appendChild(h("th", { class: "pick" }, [all]));
  state.columns.forEach((column) => {
    const sorted = state.sort && state.sort.field === column.id;
    const arrow = sorted ? (state.sort.dir === "asc" ? " ↑" : " ↓") : "";
    head.appendChild(
      h("th", {
        class: sorted ? "sorted" : "",
        text: column.name + arrow,
        title: column.id,
        onclick: () => sortBy(column.id),
      })
    );
  });
  head.appendChild(h("th", { class: "actions" }));

  const body = el("grid-body");
  clear(body);
  state.rows.forEach((row) => body.appendChild(renderRow(row)));

  el("grid-empty").hidden = state.rows.length > 0;
  el("grid").hidden = state.rows.length === 0;
  // Filtre grubu bossa suc kayitta degil sorguda; metin onu soyler.
  el("grid-empty-text").textContent =
    state.group && state.group.kind === "filter"
      ? "JQL henüz sonuç getirmedi, Güncelle'yi dene"
      : "Bu sektörde kayıt yok.";
  // Bos grupta disa aktaracak bir sey yok.
  el("export-xlsx").disabled = state.total === 0;
  el("mail-send").disabled = state.total === 0 || state.mailSupported === false;
  renderPickCount();
  el("catalog-warning").hidden = !state.catalogEmpty;
  el("row-count").textContent =
    state.shown === state.total ? `${state.total} kayıt` : `${state.shown} / ${state.total} kayıt`;
}

/** Jira durum alani mi? Kapsulu yalnizca statusCategory tasiyan hucre alir. */
function isStatusCell(cell) {
  const raw = cell.raw;
  return !!(
    cell.text &&
    raw &&
    typeof raw === "object" &&
    !Array.isArray(raw) &&
    raw.statusCategory
  );
}

function statusPill(cell) {
  const category = cell.raw.statusCategory || {};
  const known = ["new", "indeterminate", "done"].indexOf(category.key) >= 0;
  // Kategori yoksa ya da tanimadigimiz bir anahtarsa notr kapsul.
  return h("span", {
    class: "status-pill" + (known ? " status-" + category.key : ""),
    text: cell.text,
  });
}

function renderRow(row) {
  const changedFields = state.changed[row.key] || [];
  const tr = h("tr", {
    class: (row.missing ? "missing " : "") + (state.drawerKey === row.key ? "selected" : ""),
    title: row.missing ? "Bu kayıt henüz Jira'dan çekilmedi." : "",
    onclick: () => openDrawer(row.key),
  });

  const pick = h("input", {
    type: "checkbox",
    title: "Bu kaydı seç",
    onclick: (event) => {
      event.stopPropagation();
      toggleKey(row.key, event.target.checked);
    },
  });
  pick.checked = state.selectedKeys.has(row.key);
  tr.appendChild(
    h("td", { class: "pick", onclick: (event) => event.stopPropagation() }, [pick])
  );

  row.cells.forEach((cell, index) => {
    const column = state.columns[index] || {};
    const changed = changedFields.indexOf(cell.field) >= 0;
    const td = h("td", {
      class: "cell-" + cssName(cell.field) + (changed ? " changed" : ""),
      title: cell.text,
    });
    if (column.local && !column.derived) {
      renderLocalCell(td, row, cell, column);
      tr.appendChild(td);
      return;
    }
    if (cell.field === "issuekey") {
      if (row.pinned) {
        const mark = h("span", { class: "pin-mark", title: "İğnelenmiş" }, [icon("pin")]);
        td.appendChild(mark);
      }
      if (row.url) {
        td.appendChild(
          h("a", {
            href: row.url,
            target: "_blank",
            rel: "noopener",
            text: cell.text || row.key,
            onclick: (event) => event.stopPropagation(),
          })
        );
      } else {
        td.appendChild(document.createTextNode(cell.text || row.key));
      }
    } else if (isStatusCell(cell)) {
      td.appendChild(statusPill(cell));
    } else {
      td.textContent = cell.text;
    }
    tr.appendChild(td);
  });

  const isFilter = state.group && state.group.kind === "filter";
  const actions = h("div", { class: "row-actions" }, [
    h(
      "button",
      {
        title: "Son durumu sor: Teams'te mesajı hazırla",
        onclick: (event) => {
          event.stopPropagation();
          askStatus(row.key);
        },
      },
      [icon("chat")]
    ),
    h(
      "button",
      {
        title: "Bu kayıt için görev oluştur",
        onclick: (event) => {
          event.stopPropagation();
          taskFromRow(row);
        },
      },
      [icon("task")]
    ),
    isFilter
      ? h(
          "button",
          {
            class: row.pinned ? "on" : "",
            title: row.pinned ? "İğneyi kaldır" : "İğnele: güncellemede düşmesin",
            onclick: (event) => {
              event.stopPropagation();
              togglePin(row.key);
            },
          },
          [icon("pin")]
        )
      : null,
    h("button", {
      text: "×",
      title: "Gruptan çıkar",
      onclick: (event) => {
        event.stopPropagation();
        removeItem(row.key);
      },
    }),
  ]);
  tr.appendChild(h("td", { class: "actions" }, [actions]));
  return tr;
}

function sortBy(fieldId) {
  const dir =
    state.sort && state.sort.field === fieldId && state.sort.dir === "asc" ? "desc" : "asc";
  state.sort = { field: fieldId, dir };
  loadIssues();
  // Secim kalici olsun diye gruba da yazilir; yazilamazsa ekran yine dogru.
  api(`/api/groups/${state.activeId}`, {
    method: "PUT",
    body: JSON.stringify({ sort: state.sort }),
  }).catch(() => {});
}

async function togglePin(key) {
  try {
    await api(`/api/groups/${state.activeId}/items/${encodeURIComponent(key)}/pin`, {
      method: "POST",
    });
    await loadIssues();
  } catch (err) {
    fail(err);
  }
}

async function removeItem(key) {
  if (!confirm(`${key} bu gruptan çıkarılsın mı?`)) return;
  try {
    await api(`/api/groups/${state.activeId}/items/${encodeURIComponent(key)}`, { method: "DELETE" });
    if (state.drawerKey === key) closeDrawer();
    await loadIssues();
  } catch (err) {
    fail(err);
  }
}

// --- detay cekmecesi ----------------------------------------------------

async function openDrawer(key) {
  const groupId = state.activeId;
  state.drawerKey = key;
  el("drawer-key").textContent = key;
  const body = el("drawer-body");
  clear(body);
  body.appendChild(h("p", { class: "hint", text: "Okunuyor..." }));
  el("drawer").hidden = false;
  renderGrid();

  try {
    const data = await api(`/api/issues/${encodeURIComponent(key)}`);
    if (state.activeId !== groupId || state.drawerKey !== key) return;
    const link = el("drawer-link");
    link.hidden = !data.url;
    if (data.url) link.href = data.url;
    state.drawerFields = data.fields;
    state.drawerLocal = data.local || [];
    state.drawerFetchedAt = data.fetched_at;
    state.teams = emptyTeams(key);
    renderDrawerBody();
    // Teams bolumu ayri okunur: gecikirse kaydin alanlari beklemez.
    await loadTeams(key);
    if (state.activeId === groupId && state.drawerKey === key) renderDrawerBody();
  } catch (err) {
    if (state.activeId !== groupId || state.drawerKey !== key) return;
    state.drawerFields = [];
    state.drawerLocal = [];
    state.teams = null;
    clear(body);
    body.appendChild(h("p", { class: "hint", text: err.message }));
    el("drawer-link").hidden = true;
  }
}

function renderDrawerFieldsBadge() {
  const badge = el("drawer-fields-badge");
  const total = (state.drawerFields || []).length + (state.drawerLocal || []).length;
  if (!total) {
    badge.hidden = true;
    return;
  }
  const selected = state.group && state.group.detail_fields;
  const count = selected === null || selected === undefined ? total : selected.length;
  badge.hidden = false;
  badge.textContent = `${count}/${total}`;
}

function renderDrawerBody() {
  const body = el("drawer-body");
  clear(body);
  el("drawer-empty").setAttribute("aria-checked", String(state.drawerShowEmpty));
  renderDrawerFieldsBadge();
  if (state.drawerFetchedAt) {
    body.appendChild(h("p", { class: "hint", text: "Çekilme: " + stamp(state.drawerFetchedAt) }));
  } else {
    body.appendChild(
      h("p", { class: "hint", text: "Bu kayıt henüz Jira'dan çekilmedi." })
    );
  }
  renderDrawerLocal(body);
  renderDrawerTeams(body);
  const selected = state.group && state.group.detail_fields;
  (state.drawerFields || [])
    .filter((item) => selected === null || selected === undefined || selected.includes(item.field))
    .filter((item) => state.drawerShowEmpty || !item.empty)
    .forEach((item) => {
      body.appendChild(
        h("div", { class: "detail-row" + (item.empty ? " is-empty" : "") }, [
          h("div", { class: "label", text: `${item.name} (${item.field})` }),
          h("div", { class: "value", text: item.empty ? "—" : item.text }),
        ])
      );
    });
}

async function drawerFieldsModal() {
  if (!state.group || !state.drawerKey) return;
  const groupId = state.group.id;
  const groupName = state.group.name;
  const drawerKey = state.drawerKey;
  const savedFields = state.group.detail_fields;
  const loading = h("p", { class: "hint", text: "Alanlar okunuyor..." });
  openModal(
    "Detay alanları",
    loading,
    [{ label: "Vazgeç", onClick: closeModal }]
  );
  let jiraFields;
  try {
    jiraFields = (await api("/api/jira/fields")).fields;
  } catch (err) {
    if (el("modal").hidden || !loading.isConnected
        || el("modal-body").firstChild !== loading) return;
    closeModal();
    fail(err);
    return;
  }
  if (el("modal").hidden || !loading.isConnected
      || el("modal-body").firstChild !== loading) return;
  if (state.activeId !== groupId || state.drawerKey !== drawerKey) {
    closeModal();
    return;
  }
  const byId = new Map();
  jiraFields.forEach((item) => {
    byId.set(item.id, { id: item.id, name: item.name || item.id, kind: "jira" });
  });
  (state.drawerFields || []).forEach((item) => {
    if (!byId.has(item.field)) {
      byId.set(item.field, {
        id: item.field, name: item.name || item.field, kind: "jira",
      });
    }
  });
  (state.drawerLocal || []).forEach((item) => {
    byId.set(item.field, {
      id: item.field, name: item.name || item.field, kind: "local",
    });
  });
  (savedFields || []).forEach((id) => {
    if (!byId.has(id)) {
      byId.set(id, {
        id, name: id, kind: id.startsWith("local:") ? "local" : "jira",
      });
    }
  });
  const available = Array.from(byId.values()).sort((a, b) =>
    a.kind.localeCompare(b.kind) || a.name.localeCompare(b.name, "tr")
  );
  let useDefault = savedFields === null || savedFields === undefined;
  let chosen = new Set(useDefault ? available.map((item) => item.id) : savedFields);
  const list = h("div", { class: "column-list" }, []);
  const search = h("input", { type: "search", placeholder: "Alan ara" });
  const status = h("p", { class: "hint" });

  function render() {
    clear(list);
    const query = search.value.trim().toLocaleLowerCase("tr");
    status.textContent = useDefault
      ? "Varsayılan: tüm Jira ve yerel alanlar"
      : `${chosen.size} alan seçili`;
    let lastKind = null;
    available.filter((item) =>
      !query || `${item.name} ${item.id}`.toLocaleLowerCase("tr").includes(query)
    ).forEach((item) => {
      if (item.kind !== lastKind) {
        list.appendChild(h("h3", {
          text: item.kind === "local" ? "Yerel alanlar" : "Jira alanları",
        }));
        lastKind = item.kind;
      }
      const check = h("input", { type: "checkbox" });
      check.checked = chosen.has(item.id);
      check.addEventListener("change", () => {
        useDefault = false;
        if (check.checked) chosen.add(item.id);
        else chosen.delete(item.id);
        render();
      });
      list.appendChild(h("label", { class: "entry checkbox" }, [
        check,
        h("span", { text: `${item.name} (${item.id})` }),
      ]));
    });
  }
  search.addEventListener("input", render);
  const body = h("div", {}, [
    h("p", { class: "hint", text: `Filo: ${groupName}` }),
    search,
    h("div", { class: "toolbar" }, [
      h("button", { text: "Tümü", onclick: () => {
        useDefault = false;
        chosen = new Set(available.map((item) => item.id));
        render();
      } }),
      h("button", { text: "Hiçbiri", onclick: () => {
        useDefault = false;
        chosen = new Set();
        render();
      } }),
      h("button", { text: "Varsayılana dön", onclick: () => {
        useDefault = true;
        chosen = new Set(available.map((item) => item.id));
        render();
      } }),
    ]),
    status,
    list,
  ]);
  render();
  openModal("Detay alanları", body, [
    { label: "Vazgeç", onClick: closeModal },
    { label: "Kaydet", kind: "primary", onClick: async () => {
      const detailFields = useDefault ? null : Array.from(chosen);
      try {
        const data = await api(`/api/groups/${groupId}`, {
          method: "PUT",
          body: JSON.stringify({ detail_fields: detailFields }),
        });
        if (state.activeId === groupId && state.group && state.group.id === groupId) {
          state.group = data.group;
          const index = state.groups.findIndex((group) => group.id === groupId);
          if (index >= 0) state.groups[index] = data.group;
          renderDrawerBody();
        }
        closeModal();
      } catch (err) {
        fail(err);
      }
    } },
  ]);
}

function closeDrawer() {
  el("drawer").hidden = true;
  state.drawerKey = null;
  if (state.group) renderGrid();
}

// --- yerel alanlar: hucre duzenleyici, gecmis, alan yonetimi -------------

const LOCAL_TYPE_LABEL = {
  text: "Metin",
  number: "Sayı",
  date: "Tarih",
  bool: "Evet/Hayır",
  select: "Liste",
};

// Ekranda gosterilen zaman: sunucu ISO/UTC yazar, kullanici yereline cevrilir.
function stamp(iso) {
  if (!iso) return "";
  const moment = new Date(iso);
  if (isNaN(moment.getTime())) return String(iso);
  const pad = (value) => String(value).padStart(2, "0");
  return (
    `${pad(moment.getDate())}.${pad(moment.getMonth() + 1)}.${moment.getFullYear()} ` +
    `${pad(moment.getHours())}:${pad(moment.getMinutes())}`
  );
}

function renderLocalCell(td, row, cell, column) {
  td.classList.add("local-cell");
  if (!cell.text) td.classList.add("is-empty");
  td.appendChild(metinCiz(h("span", { class: "local-text" }, []), cell.text || "—"));

  const tracked = column.local.track_history || cell.changes > 0;
  if (tracked) {
    const button = h(
      "button",
      {
        class: "clock" + (cell.changes ? " on" : ""),
        title: cell.changes ? `${cell.changes} değişim` : "Henüz değişim yok",
        onclick: (event) => {
          event.stopPropagation();
          openHistory(event.currentTarget, row.key, column.local);
        },
      },
      [icon("clock")]
    );
    if (cell.changes) {
      button.appendChild(h("span", { class: "count", text: String(cell.changes) }));
    }
    td.appendChild(button);
  }

  td.addEventListener("click", (event) => {
    event.stopPropagation();
    // Acik duzenleyicinin icine tiklamak onu bastan kurmasin.
    if (state.editing && state.editing.node === td) return;
    startCellEdit(td, row, cell, column.local);
  });
}

function startCellEdit(td, row, cell, field) {
  if (state.editing) closeEditor(true);
  const editor = localEditor(field, cell.raw, {
    onSave: (value) => saveLocalValue(row.key, field, value, editor),
    onCancel: () => {
      closeEditor(false);
      renderGrid();
    },
  });
  state.editing = { node: td, editor: editor };
  clear(td);
  td.appendChild(editor.node);
  editor.focus();
}

function closeEditor(silent) {
  const editing = state.editing;
  state.editing = null;
  if (editing && !silent) editing.editor.detach();
}

/** Tipe gore satir ici duzenleyici. Enter kaydeder, Esc vazgecer, blur kaydeder. */
function localEditor(field, value, hooks, options) {
  const opts = options || {};
  // Cok satirli kip yalnizca cekmecede acilir: grid hucresi tek satir kalir.
  const wide = !!opts.multiline && field.type === "text";
  let node;
  let box;
  let dead = false;
  const read = () => (field.type === "bool" ? (node.checked ? "1" : "0") : node.value);
  const commit = () => {
    if (dead) return;
    dead = true;
    hooks.onSave(read());
  };
  const abort = () => {
    if (dead) return;
    dead = true;
    hooks.onCancel();
  };

  if (field.type === "select") {
    node = h("select", { class: "local-input" }, []);
    node.appendChild(h("option", { value: "", text: "—" }));
    (field.options || []).forEach((option) =>
      node.appendChild(h("option", { value: option, text: option }))
    );
    node.value = value || "";
    node.addEventListener("change", commit);
  } else if (field.type === "bool") {
    node = h("input", { type: "checkbox", class: "local-input local-check" });
    node.checked = value === "1";
    node.addEventListener("change", commit);
  } else if (field.type === "date") {
    node = h("input", { type: "date", class: "local-input" });
    node.value = value || "";
    node.addEventListener("change", commit);
  } else if (wide) {
    // Cekmecedeki uzun metin alani: Enter yeni satir acar, Ctrl+Enter kaydeder.
    node = h("textarea", {
      class: "local-input local-area",
      rows: "4",
      title: "Ctrl+Enter kaydeder, Esc vazgeçer",
    });
    node.value = value || "";
    box = duzeltKutusu(node, field.name);
  } else {
    // Sayi icin de metin kutusu: tarayicinin number girdisi Turkce ondalik
    // virgulu yutuyor, dogrulamayi sunucu yapiyor.
    node = h("input", {
      type: "text",
      class: "local-input",
      inputmode: field.type === "number" ? "decimal" : null,
    });
    node.value = value || "";
  }

  node.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      // Cok satirli alanda Enter yeni satir acar; kaydeden Ctrl+Enter'dir.
      if (wide && !(event.ctrlKey || event.metaKey)) return;
      event.preventDefault();
      commit();
    } else if (event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      abort();
    }
  });
  node.addEventListener("blur", () => {
    // Hata gosterilirken odak kaybi kaydi tekrar denemesin.
    if (node.classList.contains("bad")) return;
    // "Düzelt" paneli aciksa odak kaybi kaydi tetiklemesin: panel kapaninca
    // ya da oneri uygulaninca normal akis devam eder.
    if (node.duzeltAktif) return;
    commit();
  });

  return {
    node: node,
    // Mount edilecek dugum: "Düzelt" sarmalayicisi varsa o, yoksa alanin kendisi.
    box: box || node,
    focus: () => {
      node.focus();
      // Cok satirli alanda metnin tumu secili gelirse ilk tusa basildiginda
      // silinir; imlec sona konur. Tek satirda secmek hizli degistirmeyi
      // kolaylastiriyor, orada eski davranis kaliyor.
      if (wide) {
        try {
          node.setSelectionRange(node.value.length, node.value.length);
        } catch (err) {
          // Tarayici desteklemiyorsa odak yeter.
        }
      } else if (node.select) {
        node.select();
      }
    },
    detach: () => {},
    fail: (message) => {
      dead = false;
      node.classList.add("bad");
      node.title = message;
      const holder = node.parentNode;
      if (holder && !holder.querySelector(".local-error")) {
        holder.appendChild(h("span", { class: "local-error", text: message }));
      }
      node.focus();
    },
    clean: () => {
      node.classList.remove("bad");
      const holder = node.parentNode;
      const note = holder && holder.querySelector(".local-error");
      if (note) note.remove();
    },
  };
}

async function saveLocalValue(key, field, value, editor) {
  try {
    await api(`/api/issues/${encodeURIComponent(key)}/local/${field.id}`, {
      method: "PUT",
      body: JSON.stringify({ value: value }),
    });
    if (editor) editor.clean();
    state.editing = null;
    if (state.drawerKey === key) await openDrawer(key);
    await loadIssues();
  } catch (err) {
    if (editor) editor.fail(err.message);
    else fail(err);
  }
}

// --- gecmis popover -----------------------------------------------------

async function openHistory(anchor, key, field) {
  const box = el("history-popover");
  el("history-title").textContent = `${field.name} · ${key}`;
  const body = el("history-body");
  clear(body);
  body.appendChild(h("p", { class: "hint", text: "Okunuyor..." }));
  historyFootShow(true);
  box.hidden = false;
  placePopover(box, anchor);
  state.popover = { key: key, field: field };

  try {
    const data = await api(`/api/issues/${encodeURIComponent(key)}/local/${field.id}/history`);
    renderHistory(data.entries || []);
    placePopover(box, anchor);
  } catch (err) {
    clear(body);
    body.appendChild(h("p", { class: "hint", text: err.message }));
  }
}

/**
 * Gorevin "son durum" defteri.
 *
 * Jira yerel alan gecmisiyle AYNI kaliba cizilir: eskiden yeniye bloklar,
 * ilk giriste "ilk değer" rozeti, son giris vurgulu, 20'den fazlasi katlanir.
 * Tek fark: defter silinmez, bu yuzden satir silme ve "Geçmişi temizle" yok.
 */
async function openTaskHistory(anchor, task) {
  const box = el("history-popover");
  el("history-title").textContent = `Son durum · ${task.title}`;
  const body = el("history-body");
  clear(body);
  body.appendChild(h("p", { class: "hint", text: "Okunuyor..." }));
  historyFootShow(false);
  box.hidden = false;
  placePopover(box, anchor);
  state.popover = { task: task };

  try {
    const data = await api(`/api/tasks/${task.id}/son-durum-gecmisi`);
    // Sunucu eskiden yeniye yollar: cizim sirasi da aynidir.
    const chrono = (data.entries || []).map((entry) => ({
      id: entry.id,
      changed_at: entry.olusturma,
      new_text: entry.metin,
    }));
    clear(body);
    if (!chrono.length) {
      body.appendChild(h("p", { class: "hint", text: "Bu görevde henüz son durum yazılmadı." }));
      return;
    }
    paintHistory(body, chrono);
    placePopover(box, anchor);
  } catch (err) {
    clear(body);
    body.appendChild(h("p", { class: "hint", text: err.message }));
  }
}

// Sunucu en yeniden en eskiye yollar (bkz. repository.list_local_history); popover
// icin zaman cizelgesi olarak eskiden yeniye cevrilir.
const HISTORY_FOLD_AT = 20;
const HISTORY_FOLD_TAIL = 20;

function renderHistory(entries) {
  const body = el("history-body");
  el("history-clear").disabled = entries.length === 0;
  clear(body);
  if (!entries.length) {
    body.appendChild(h("p", { class: "hint", text: "Bu hücrede henüz değişim yok." }));
    return;
  }
  paintHistory(body, entries.slice().reverse());
}

/** chrono: eskiden yeniye sirali girdiler. Cok girdi varsa en eskiler katlanir. */
function paintHistory(body, chrono, expanded) {
  clear(body);
  const total = chrono.length;
  const showAll = expanded || total <= HISTORY_FOLD_AT;
  const visible = showAll ? chrono : chrono.slice(total - HISTORY_FOLD_TAIL);
  const hiddenCount = total - visible.length;
  if (hiddenCount > 0) {
    body.appendChild(
      h("button", {
        class: "history-fold",
        text: `… önceki ${hiddenCount} değişikliği göster`,
        onclick: () => paintHistory(body, chrono, true),
      })
    );
  }
  visible.forEach((entry, index) => {
    const position = hiddenCount + index;
    body.appendChild(historyBlock(entry, position === 0, position === total - 1));
  });
}

function historyBlock(entry, isFirst, isLast) {
  const who = entry.changed_by || entry.actor || entry.who || "";
  const meta = [stamp(entry.changed_at)];
  if (who) meta.push(who);
  const value = entry.new_text || "";
  return h("div", { class: "history-entry" + (isLast ? " current" : "") }, [
    h("div", { class: "history-meta" }, [
      h("span", { class: "when", text: meta.join(" · ") }),
      isFirst ? h("span", { class: "history-badge", text: "ilk değer" }) : null,
      // Gorevin son durum defteri silinmez; silme dugmesi yalnizca yerel
      // alan gecmisinde durur.
      state.popover && state.popover.field
        ? h("button", {
            class: "drop",
            text: "×",
            title: "Bu satırı sil",
            onclick: () => dropHistoryEntry(entry.id),
          })
        : null,
    ]),
    metinCiz(h("div", { class: "history-value" }, []), value || "— (boş)"),
  ]);
}

async function dropHistoryEntry(historyId) {
  const target = state.popover;
  if (!target || !target.field) return;
  try {
    await api(
      `/api/issues/${encodeURIComponent(target.key)}/local/${target.field.id}/history/${historyId}`,
      { method: "DELETE" }
    );
    const data = await api(
      `/api/issues/${encodeURIComponent(target.key)}/local/${target.field.id}/history`
    );
    renderHistory(data.entries || []);
    await loadIssues();
    if (state.drawerKey === target.key) await openDrawer(target.key);
  } catch (err) {
    fail(err);
  }
}

async function clearHistory() {
  const target = state.popover;
  if (!target || !target.field) return;
  if (!confirm(`${target.key} için "${target.field.name}" geçmişi tamamen silinsin mi?`)) return;
  try {
    await api(`/api/issues/${encodeURIComponent(target.key)}/local/${target.field.id}/history`, {
      method: "DELETE",
    });
    closeHistory();
    await loadIssues();
    if (state.drawerKey === target.key) await openDrawer(target.key);
  } catch (err) {
    fail(err);
  }
}

function placePopover(box, anchor) {
  const spot = anchor.getBoundingClientRect();
  const width = box.offsetWidth || 320;
  const left = Math.max(8, Math.min(spot.left, window.innerWidth - width - 8));
  const below = spot.bottom + 6;
  const height = box.offsetHeight || 200;
  const top = below + height > window.innerHeight ? Math.max(8, spot.top - height - 6) : below;
  box.style.left = left + "px";
  box.style.top = top + "px";
}

/** Popover'in alt seridi: defter silinmeyen gorunumde hic gorunmez. */
function historyFootShow(acik) {
  const button = el("history-clear");
  button.hidden = !acik;
  const foot = button.parentNode;
  if (foot) foot.hidden = !acik;
}

function closeHistory() {
  el("history-popover").hidden = true;
  state.popover = null;
}

// --- detay cekmecesindeki yerel bolum -----------------------------------

function renderDrawerLocal(body) {
  const selected = state.group && state.group.detail_fields;
  const items = (state.drawerLocal || []).filter(
    (item) => selected === null || selected === undefined || selected.includes(item.field)
  );
  if (!items.length) return;
  const key = state.drawerKey;
  const section = h("section", { class: "drawer-local" }, [
    h("h3", { text: "Yerel alanlar" }),
  ]);

  items.forEach((item) => {
    const value = h("div", { class: "value" }, []);
    const show = () => {
      clear(value);
      value.appendChild(
        metinCiz(
          h("button", { class: "local-open", title: "Düzenle", onclick: () => edit() }, []),
          item.text || "—"
        )
      );
    };
    const edit = () => {
      clear(value);
      const editor = localEditor(
        item,
        item.value,
        {
          onSave: (fresh) => saveLocalValue(key, item, fresh, editor),
          onCancel: show,
        },
        // Cekmecede yer var: metin alanlari cok satirli acilir ve kosesinde
        // "Düzelt" dugmesi durur.
        { multiline: true }
      );
      value.appendChild(editor.box);
      editor.focus();
    };
    show();

    const head = h("div", { class: "label" }, [
      h("span", { text: `${item.name} (${item.type_label})` }),
    ]);
    if (item.track_history || item.changes) {
      head.appendChild(h("span", { class: "badge", text: `${item.changes} değişim` }));
    }

    const rowBox = h("div", { class: "detail-row" + (item.empty ? " is-empty" : "") }, [head, value]);
    if (item.history && item.history.length) {
      const list = h("div", { class: "history-inline" }, []);
      item.history.forEach((entry) => {
        list.appendChild(
          h("div", { class: "history-line" }, [
            h("span", { class: "when", text: stamp(entry.changed_at) }),
            h("span", { class: "what" }, [
              metinCiz(h("span", { class: "old" }, []), entry.old_text || "—"),
              document.createTextNode(" → "),
              metinCiz(h("span", { class: "new" }, []), entry.new_text || "—"),
            ]),
          ])
        );
      });
      rowBox.appendChild(list);
    }
    section.appendChild(rowBox);
  });

  body.appendChild(section);
}

// --- Teams (derin baglanti) ---------------------------------------------
//
// Mesaj Teams'in yazma kutusuna KONUR, Gonder'e kullanici basar: uygulama
// gonderimi ne yapar ne de dogrulayabilir. Hedef yalnizca kisilerdir; tek
// kisi dogrudan sohbet, birden fazlasi grup sohbeti acar.

const CONTACT_SUGGEST_MS = 200;

function emptyTeams(key) {
  return {
    key: key || null,
    contacts: [],
    templates: [],
    messages: [],
    book: [],
    bookEmpty: false,
    templateId: null,
    body: "",
    ready: false,
  };
}

/** Cekmecenin Teams bolumu icin gereken her sey tek turda okunur. */
async function loadTeams(key) {
  const path = `/api/issues/${encodeURIComponent(key)}`;
  const quiet = (promise, fallback) => promise.catch(() => fallback);
  const [contacts, templates, messages, preview] = await Promise.all([
    quiet(api(`${path}/contacts`), { contacts: [] }),
    quiet(api("/api/templates"), { templates: [] }),
    quiet(api(`${path}/messages`), { messages: [] }),
    quiet(api(`${path}/message-preview`, { method: "POST", body: "{}" }), { message: "" }),
  ]);
  const list = templates.templates || [];
  const chosen = list.find((item) => item.is_default) || list[0] || null;
  state.teams = {
    key: key,
    contacts: contacts.contacts || [],
    templates: list,
    messages: messages.messages || [],
    book: [],
    bookEmpty: false,
    templateId: chosen ? chosen.id : null,
    body: preview.message || "",
    ready: true,
  };
}

function renderDrawerTeams(parent) {
  const data = state.teams;
  if (!data || !data.ready || data.key !== state.drawerKey) return;

  const section = h("section", { class: "drawer-teams" }, [h("h3", { text: "Teams" })]);
  section.appendChild(teamsChips(data));
  section.appendChild(teamsAddBox(data));
  section.appendChild(teamsMessageBox(data));
  section.appendChild(
    h("p", {
      class: "hint",
      text: "Mesaj Teams'te hazır açılır, göndermek için Gönder'e basmanız gerekir.",
    })
  );
  section.appendChild(teamsSentBox(data));
  parent.appendChild(section);
}

function teamsChips(data) {
  const chips = h("div", { class: "teams-chips" }, []);
  if (!data.contacts.length) {
    chips.appendChild(h("span", { class: "hint", text: "Bu kayda henüz kişi eklenmedi." }));
    return chips;
  }
  data.contacts.forEach((contact) => {
    const chip = h("span", { class: "teams-chip", title: contact.email }, [
      h("span", { class: "chip-name", text: contact.name || contact.email }),
    ]);
    // Dagitim listesine yazmak kisiye yazmakla ayni sey degil; rozet uyarir.
    if (contact.kind === "list") {
      chip.appendChild(h("span", { class: "chip-kind", text: "liste" }));
    }
    chip.appendChild(
      h("button", {
        text: "×",
        title: "Kişiyi kaldır",
        onclick: () =>
          writeTeamsContacts(data.contacts.filter((item) => item.email !== contact.email)),
      })
    );
    chips.appendChild(chip);
  });
  return chips;
}

function teamsAddBox(data) {
  const emailInput = h("input", {
    type: "text",
    placeholder: "E-posta ya da ad",
    autocomplete: "off",
    list: "teams-contact-book",
  });
  const book = h("datalist", { id: "teams-contact-book" }, []);
  const nameInput = h("input", { type: "text", placeholder: "Ad (isteğe bağlı)" });
  const nameBox = h("div", { class: "teams-name" }, [nameInput]);
  nameBox.hidden = true;
  const note = h("p", { class: "hint", text: "" });

  const known = (typed) => {
    const marker = typed.trim().toLowerCase();
    return (data.book || []).find(
      (item) => item.email === marker || (item.name || "").toLowerCase() === marker
    );
  };

  let timer = null;
  const suggest = async () => {
    const typed = emailInput.value.trim();
    try {
      const found = await api(`/api/contacts?q=${encodeURIComponent(typed)}`);
      data.book = found.contacts || [];
      if (!typed) data.bookEmpty = data.book.length === 0;
    } catch (err) {
      data.book = [];
    }
    clear(book);
    data.book.forEach((item) =>
      book.appendChild(
        h("option", {
          value: item.email,
          label: (item.name || item.email) + (item.kind === "list" ? " (liste)" : ""),
        })
      )
    );
    // Defterde olmayan yeni bir adres: ad sorulur, zorunlu degildir.
    nameBox.hidden = !typed || !!known(typed);
    if (!typed && data.bookEmpty) {
      note.textContent = "Rehber boş — Ayarlar → Teams → Rehberi Outlook'tan yenile.";
    } else {
      note.textContent =
        !typed || known(typed)
          ? ""
          : "Bu kişi adres defterinde yok; ad yazarsanız deftere de düşer.";
    }
  };
  emailInput.addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(suggest, CONTACT_SUGGEST_MS);
  });
  suggest();

  const add = () => {
    const typed = emailInput.value.trim();
    if (!typed) return;
    const found = known(typed);
    const email = (found ? found.email : typed).toLowerCase();
    const name = nameInput.value.trim() || (found ? found.name : "");
    const next = data.contacts.filter((item) => item.email !== email);
    next.push({ email: email, name: name });
    writeTeamsContacts(next);
  };
  emailInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      add();
    }
  });
  nameInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      add();
    }
  });

  return h("div", { class: "teams-add" }, [
    h("div", { class: "teams-add-row" }, [
      emailInput,
      book,
      h("button", { class: "small", text: "Ekle", onclick: add }),
    ]),
    nameBox,
    note,
  ]);
}

function teamsMessageBox(data) {
  const select = h("select", {
    onchange: (event) => pickTemplate(event.target.value),
  });
  (data.templates || []).forEach((item) =>
    select.appendChild(h("option", { value: String(item.id), text: item.name }))
  );
  if (!data.templates.length) {
    select.appendChild(h("option", { value: "", text: "— şablon yok —" }));
  }
  select.value = data.templateId ? String(data.templateId) : "";

  const area = h("textarea", { class: "teams-body", rows: "4" });
  area.value = data.body || "";
  area.addEventListener("input", () => {
    data.body = area.value;
  });

  return h("div", { class: "teams-message" }, [
    h("div", { class: "teams-message-head" }, [
      h("span", { class: "label", text: "Şablon" }),
      select,
    ]),
    area,
    h("button", {
      class: "primary",
      text: "Teams'te aç",
      onclick: () => sendTeamsLink(data.key, { body: area.value }),
    }),
  ]);
}

function teamsSentBox(data) {
  const box = h("div", { class: "teams-sent" }, [h("h4", { text: "Gönderilenler" })]);
  if (!data.messages.length) {
    box.appendChild(h("p", { class: "hint", text: "Henüz mesaj açılmadı." }));
    return box;
  }
  data.messages.forEach((item) => {
    box.appendChild(
      h("div", { class: "sent-line", title: item.body }, [
        h("span", { class: "when", text: stamp(item.opened_at) }),
        h("span", { class: "who", text: item.target_text, title: "Kişiler" }),
        h("span", { class: "what", text: item.first_line }),
      ])
    );
  });
  return box;
}

async function writeTeamsContacts(contacts) {
  const key = state.drawerKey;
  try {
    const data = await api(`/api/issues/${encodeURIComponent(key)}/contacts`, {
      method: "PUT",
      body: JSON.stringify({ contacts: contacts }),
    });
    state.teams.contacts = data.contacts || [];
    renderDrawerBody();
    // Sanal sutun secili olabilir: grid de tazelensin.
    if (state.group) loadIssues();
  } catch (err) {
    fail(err);
  }
}

async function pickTemplate(value) {
  const key = state.drawerKey;
  state.teams.templateId = value ? Number(value) : null;
  try {
    const data = await api(`/api/issues/${encodeURIComponent(key)}/message-preview`, {
      method: "POST",
      body: JSON.stringify({ template_id: state.teams.templateId }),
    });
    state.teams.body = data.message || "";
  } catch (err) {
    fail(err);
  }
  renderDrawerBody();
}

/** Mesaji hazirlar ve Teams'i acar.
 *
 * Acma isini SUNUCU yapar (`msteams:` adresi isletim sistemine verilir):
 * tarayicidan acmak geride bos bir sekme birakiyordu. Sunucu acamazsa yanit
 * `opened: false` doner ve eski yola, `window.open`'a duseriz.
 */
async function sendTeamsLink(key, payload) {
  try {
    const data = await api(`/api/issues/${encodeURIComponent(key)}/teams-open`, {
      method: "POST",
      body: JSON.stringify(payload || {}),
    });
    // Pano yalnizca kirpma durumunda devreye girer: adres sinirina sigmayan
    // metnin tamami kaybolmasin.
    const copied = data.truncated ? await copyText(data.clipboard || data.message) : false;
    if (!data.opened) window.open(data.url, "_blank", "noopener");

    const trimmed = copied
      ? "Mesaj adres sınırına sığmadı ve kısaltıldı; tamamı panoya kopyalandı."
      : "Mesaj adres sınırına sığmadı ve kısaltıldı.";
    toast(
      data.opened ? "Teams'te açıldı" : "Teams açılıyor",
      data.truncated ? trimmed : "Göndermek için Teams'te Gönder'e basın.",
      data.truncated ? "" : "ok"
    );

    if (state.drawerKey === key && state.teams && state.teams.ready) {
      const fresh = await api(`/api/issues/${encodeURIComponent(key)}/messages`);
      state.teams.messages = fresh.messages || [];
      renderDrawerBody();
    }
    return data;
  } catch (err) {
    fail(err);
    return null;
  }
}

/** Grid satiri ve kanban karti: tek tikla "son durumu sor". */
async function askStatus(key) {
  try {
    const contacts = (await api(`/api/issues/${encodeURIComponent(key)}/contacts`)).contacts || [];
    if (contacts.length) {
      await sendTeamsLink(key, {});
      return;
    }
    // Hedef yok: kullanici once kisi eklesin, cekmecenin Teams bolumu acilir.
    await openDrawer(key);
    toast("Teams kişisi yok", "Bu kayda önce bir kişi ekleyin.", "");
  } catch (err) {
    fail(err);
  }
}

// --- Alanlar ekrani -----------------------------------------------------

async function localFieldsModal() {
  let fields = [];
  try {
    fields = (await api("/api/local-fields")).fields;
  } catch (err) {
    fail(err);
    return;
  }

  const list = h("div", { class: "column-list" }, []);

  function render() {
    clear(list);
    if (!fields.length) {
      list.appendChild(
        h("div", {
          class: "empty-note",
          text: "Henüz yerel alan yok. 'Yeni alan' ile kendi bilginizi eklemeye başlayın.",
        })
      );
      return;
    }
    fields.forEach((field, index) => {
      list.appendChild(
        h("div", { class: "entry" }, [
          h("span", { class: "name", text: field.name, title: field.column_id }),
          h("span", { class: "id", text: field.type_label }),
          h("span", {
            class: "badge" + (field.track_history ? " on" : ""),
            text: field.track_history ? "geçmiş açık" : "geçmiş kapalı",
          }),
          h("span", { class: "id", text: `${field.group_count} grup` }),
          h("button", {
            text: "↑",
            title: "Yukarı",
            disabled: index === 0,
            onclick: () => reorderFields(index, -1),
          }),
          h("button", {
            text: "↓",
            title: "Aşağı",
            disabled: index === fields.length - 1,
            onclick: () => reorderFields(index, 1),
          }),
          h("button", { text: "Düzenle", onclick: () => fieldForm(field) }),
          h("button", { class: "danger", text: "Sil", onclick: () => dropField(field) }),
        ])
      );
    });
  }

  async function reload() {
    fields = (await api("/api/local-fields")).fields;
    render();
    await loadIssues();
  }

  async function reorderFields(index, delta) {
    const ids = fields.map((field) => field.id);
    const target = index + delta;
    if (target < 0 || target >= ids.length) return;
    const swap = ids[index];
    ids[index] = ids[target];
    ids[target] = swap;
    try {
      fields = (await api("/api/local-fields/reorder", {
        method: "POST",
        body: JSON.stringify({ ids: ids }),
      })).fields;
      render();
    } catch (err) {
      fail(err);
    }
  }

  async function dropField(field) {
    const warning =
      `"${field.name}" alanı silinsin mi?\n` +
      `${field.value_count} kayıttaki değer ve ${field.history_count} geçmiş satırı silinecek.`;
    if (!confirm(warning)) return;
    try {
      await api(`/api/local-fields/${field.id}`, { method: "DELETE" });
      await reload();
      toast("Alan silindi", `"${field.name}" ve bağlı değerleri kaldırıldı.`, "ok");
    } catch (err) {
      fail(err);
    }
  }

  function fieldForm(existing) {
    const nameInput = h("input", { type: "text", value: existing ? existing.name : "" });
    const typeSelect = h("select", {}, []);
    Object.keys(LOCAL_TYPE_LABEL).forEach((name) =>
      typeSelect.appendChild(h("option", { value: name, text: LOCAL_TYPE_LABEL[name] }))
    );
    typeSelect.value = existing ? existing.type : "text";
    if (existing) typeSelect.disabled = true;

    const optionsInput = h("textarea", { placeholder: "Her satıra bir seçenek" });
    if (existing) optionsInput.value = (existing.options || []).join("\n");
    const optionsField = field("Seçenekler", optionsInput);
    const syncOptions = () => {
      optionsField.hidden = typeSelect.value !== "select";
    };
    typeSelect.addEventListener("change", syncOptions);
    syncOptions();

    const historyInput = h("input", { type: "checkbox" });
    historyInput.checked = existing ? existing.track_history : false;
    const historyBox = h("label", { class: "checkbox" }, [
      historyInput,
      document.createTextNode("Geçmişi tut (her değişim kaydedilir)"),
    ]);

    const body = h("div", {}, [
      field("Ad", nameInput),
      field("Tip", typeSelect),
      optionsField,
      field("Geçmiş", historyBox),
      h("p", {
        class: "hint",
        text:
          "Yerel alanlar Jira'ya gitmez, Güncelle bunları ezmez. " +
          (existing ? "Tip sonradan değiştirilemez." : ""),
      }),
    ]);

    const save = async () => {
      const payload = {
        name: nameInput.value,
        track_history: historyInput.checked,
        options: optionsInput.value
          .split("\n")
          .map((line) => line.trim())
          .filter((line) => line),
      };
      if (!existing) payload.type = typeSelect.value;
      try {
        if (existing) await api(`/api/local-fields/${existing.id}`, { method: "PUT", body: JSON.stringify(payload) });
        else await api("/api/local-fields", { method: "POST", body: JSON.stringify(payload) });
        await reload();
        openList();
      } catch (err) {
        fail(err);
      }
    };

    openModal(existing ? "Alanı düzenle" : "Yeni alan", body, [
      { label: "Geri", onClick: openList },
      { label: "Kaydet", kind: "primary", onClick: save },
    ]);
  }

  function openList() {
    render();
    openModal("Yerel alanlar", h("div", {}, [
      h("p", {
        class: "hint",
        text:
          "Buradaki alanlar bütün gruplarda ortaktır, değer kayıt başına tektir. " +
          "Hangi grupta görüneceğini 'Sütunlar' ekranından seçersiniz.",
      }),
      list,
    ]), [
      { label: "Yeni alan", onClick: () => fieldForm(null) },
      { label: "Kapat", kind: "primary", onClick: closeModal },
    ]);
  }

  openList();
}

// --- grup olusturma / duzenleme ----------------------------------------

function groupModal(existing) {
  const nameInput = h("input", { type: "text", value: existing ? existing.name : "" });
  const jqlInput = h("textarea", { placeholder: "project = DEMO AND status = Açık" });
  if (existing) jqlInput.value = existing.jql || "";

  const jqlField = field("JQL", jqlInput);
  jqlField.hidden = !(existing && existing.kind === "filter");

  const kindBox = h("div", { class: "radio-row" }, []);
  const radios = ["manual", "filter"].map((kind) => {
    const input = h("input", {
      type: "radio",
      name: "group-kind",
      value: kind,
      checked: existing ? existing.kind === kind : kind === "manual",
      onchange: () => {
        jqlField.hidden = kind !== "filter";
      },
    });
    kindBox.appendChild(h("label", {}, [input, document.createTextNode(KIND_LABEL[kind])]));
    return input;
  });

  let color = existing ? existing.color : "blue";
  const picker = h("div", { class: "color-picker" }, []);
  SABER_COLORS.forEach((name) => {
    picker.appendChild(
      h("button", {
        class: "color-" + name + (name === color ? " on" : ""),
        title: name,
        onclick: () => {
          color = name;
          Array.from(picker.children).forEach((child) =>
            child.classList.toggle("on", child.title === name)
          );
        },
      })
    );
  });

  const body = h("div", {}, [
    field("Ad", nameInput),
    field("Tür", kindBox),
    jqlField,
    field("Renk", picker),
  ]);

  const save = async () => {
    const kind = radios.find((input) => input.checked).value;
    const payload = {
      name: nameInput.value,
      kind: kind,
      jql: kind === "filter" ? jqlInput.value : "",
      color: color,
    };
    try {
      const data = existing
        ? await api(`/api/groups/${existing.id}`, { method: "PUT", body: JSON.stringify(payload) })
        : await api("/api/groups", { method: "POST", body: JSON.stringify(payload) });
      closeModal();
      await loadGroups(data.group.id);
    } catch (err) {
      fail(err);
    }
  };

  openModal(existing ? "Filoyu düzenle" : "Yeni Filo", body, [
    { label: "Vazgeç", onClick: closeModal },
    { label: "Kaydet", kind: "primary", onClick: save },
  ]);
}

async function deleteGroup() {
  if (!state.group) return;
  if (!confirm(`"${state.group.name}" grubu silinsin mi? Kayıtlar veritabanında kalır.`)) return;
  try {
    await api(`/api/groups/${state.group.id}`, { method: "DELETE" });
    state.activeId = null;
    await loadGroups();
  } catch (err) {
    fail(err);
  }
}

// --- kayit ekleme -------------------------------------------------------

function addItemsModal() {
  const area = h("textarea", {
    placeholder: "DEMO-1 DEMO-2\nhttps://jira.example.com/browse/DEMO-3",
  });
  const body = h("div", {}, [
    h("p", {
      class: "hint",
      text:
        "Anahtarları yapıştırın. Satır, virgül ya da boşluk fark etmez; " +
        "Jira bağlantılarının içinden de anahtar okunur.",
    }),
    area,
  ]);

  const save = async () => {
    try {
      const data = await api(`/api/groups/${state.activeId}/items`, {
        method: "POST",
        body: JSON.stringify({ text: area.value }),
      });
      closeModal();
      await loadIssues();
      const parts = [
        `${data.added.length} eklendi`,
        `${data.already.length} zaten vardı`,
        `${data.invalid.length} anlaşılmadı`,
      ];
      const extra = data.invalid.length
        ? [h("span", { text: "Anlaşılmayan: " + data.invalid.join(", ") })]
        : [];
      toast("Kayıt ekleme", parts.join(" · "), data.added.length ? "ok" : "", extra);
    } catch (err) {
      fail(err);
    }
  };

  openModal("Kayıt ekle", body, [
    { label: "Vazgeç", onClick: closeModal },
    { label: "Ekle", kind: "primary", onClick: save },
  ]);
}

// --- sutun secici -------------------------------------------------------

async function columnsModal() {
  try {
    state.fields = (await api("/api/fields")).fields;
  } catch (err) {
    fail(err);
    return;
  }

  let chosen = state.columns.map((column) => column.id);
  const chosenBox = h("div", { class: "column-list" }, []);
  const availableBox = h("div", { class: "column-list" }, []);
  const search = h("input", { type: "text", placeholder: "Alan ara" });
  const byId = {};
  state.fields.forEach((item) => {
    byId[item.id] = item;
  });
  const nameOf = (id) => (byId[id] ? byId[id].name : id);

  function renderChosen() {
    clear(chosenBox);
    if (!chosen.length) {
      chosenBox.appendChild(h("div", { class: "empty-note", text: "Sütun seçilmedi." }));
    }
    chosen.forEach((id, index) => {
      const known = byId[id];
      chosenBox.appendChild(
        h("div", { class: "entry" + (known && known.kind === "derived" ? " derived" : "") }, [
          h("div", { class: "label" }, [
            h("span", { class: "name", text: nameOf(id) }),
            h("span", { class: "id", text: id }),
          ]),
          h("button", {
            text: "↑",
            title: "Yukarı",
            disabled: index === 0,
            onclick: () => {
              const swap = chosen[index - 1];
              chosen[index - 1] = chosen[index];
              chosen[index] = swap;
              renderChosen();
            },
          }),
          h("button", {
            text: "↓",
            title: "Aşağı",
            disabled: index === chosen.length - 1,
            onclick: () => {
              const swap = chosen[index + 1];
              chosen[index + 1] = chosen[index];
              chosen[index] = swap;
              renderChosen();
            },
          }),
          h("button", {
            text: "×",
            title: "Çıkar",
            onclick: () => {
              chosen = chosen.filter((item) => item !== id);
              renderChosen();
              renderAvailable();
            },
          }),
        ])
      );
    });
  }

  function renderAvailable() {
    clear(availableBox);
    const needle = search.value.trim().toLocaleLowerCase("tr");
    const list = state.fields
      .filter((item) => chosen.indexOf(item.id) < 0)
      .filter(
        (item) =>
          !needle ||
          item.name.toLocaleLowerCase("tr").indexOf(needle) >= 0 ||
          item.id.toLowerCase().indexOf(needle) >= 0
      );
    const jira = list.filter((item) => item.kind !== "local" && item.kind !== "derived");
    const local = list.filter((item) => item.kind === "local" || item.kind === "derived");

    if (!list.length) {
      availableBox.appendChild(
        h("div", {
          class: "empty-note",
          text: state.fields.length
            ? "Eşleşen alan yok."
            : "Alan kataloğu boş. Ayarlar ekranından çekin ya da bir kez Güncelle çalıştırın.",
        })
      );
    }

    const add = (item) => {
      availableBox.appendChild(
        h("div", { class: "entry" + (item.kind === "derived" ? " derived" : "") }, [
          h("div", { class: "label" }, [
            h("span", { class: "name", text: item.name }),
            h("span", { class: "id", text: item.id }),
          ]),
          h("button", {
            text: "+",
            title: "Ekle",
            onclick: () => {
              chosen.push(item.id);
              renderChosen();
              renderAvailable();
            },
          }),
        ])
      );
    };

    jira.slice(0, 200).forEach(add);
    if (local.length) {
      availableBox.appendChild(h("div", { class: "list-head", text: "Yerel alanlar" }));
      local.forEach(add);
    }
  }

  search.addEventListener("input", renderAvailable);
  renderChosen();
  renderAvailable();

  const body = h("div", { class: "column-editor" }, [
    h("div", {}, [h("h3", { text: "Seçili sütunlar" }), chosenBox]),
    h("div", {}, [h("h3", { text: "Alanlar" }), search, availableBox]),
  ]);

  const save = async () => {
    try {
      await api(`/api/groups/${state.activeId}`, {
        method: "PUT",
        body: JSON.stringify({ columns: chosen }),
      });
      closeModal();
      await loadIssues();
    } catch (err) {
      fail(err);
    }
  };

  const makeDefault = async () => {
    try {
      await api("/api/settings/columns", { method: "PUT", body: JSON.stringify({ columns: chosen }) });
      toast("Sütunlar", "Genel varsayılan güncellendi.", "ok");
    } catch (err) {
      fail(err);
    }
  };

  openModal(
    "Sütunlar",
    body,
    [
      { label: "Genel varsayılan yap", onClick: makeDefault },
      { label: "Vazgeç", onClick: closeModal },
      { label: "Uygula", kind: "primary", onClick: save },
    ],
    { wide: true }
  );
}

// --- Excel'e aktar ------------------------------------------------------

function exportModal() {
  if (!state.group || !state.columns.length) return;

  const list = h("div", { class: "export-columns" }, []);
  const boxes = state.columns.map((column) => {
    const input = h("input", { type: "checkbox" });
    input.checked = true;
    list.appendChild(
      h("label", { class: "checkbox" }, [input, document.createTextNode(column.name)])
    );
    return { id: column.id, input: input };
  });
  const setAll = (value) => boxes.forEach((item) => (item.input.checked = value));

  const historyInput = h("input", { type: "checkbox" });
  const filterInput = h("input", { type: "checkbox" });
  filterInput.checked = true;

  // Grid'de onay kutusuyla secim varsa varsayilan olarak yalniz onlar gider.
  const picked = selectedKeyList();
  const pickedInput = h("input", { type: "checkbox" });
  pickedInput.checked = picked.length > 0;
  const pickedBox = picked.length
    ? h("label", { class: "checkbox" }, [
        pickedInput,
        document.createTextNode(`Yalnız seçili ${picked.length} kayıt`),
      ])
    : null;

  const body = h("div", {}, [
    h("div", { class: "export-head" }, [
      h("span", { class: "hint", text: "Excel'e gidecek sütunlar" }),
      h("button", { class: "small", text: "Tümünü seç", onclick: () => setAll(true) }),
      h("button", { class: "small", text: "Temizle", onclick: () => setAll(false) }),
    ]),
    list,
    h("label", { class: "checkbox" }, [
      historyInput,
      document.createTextNode("Geçmiş sayfasını ekle"),
    ]),
    h("label", { class: "checkbox" }, [
      filterInput,
      document.createTextNode("Görünen süzgeç ve sıralamayı uygula"),
    ]),
    pickedBox,
    h("p", {
      class: "hint",
      text: "Dosya ekranda görüneni yazar; uzun metinler kırpılmaz.",
    }),
  ]);

  const start = () => {
    const columns = boxes.filter((item) => item.input.checked).map((item) => item.id);
    if (!columns.length) {
      toast("Excel'e aktar", "En az bir sütun seçin.", "error");
      return;
    }
    const params = new URLSearchParams();
    params.set("columns", columns.join(","));
    if (historyInput.checked) params.set("history", "1");
    if (filterInput.checked) {
      if (state.query) params.set("q", state.query);
      if (state.sort) {
        params.set("sort", state.sort.field);
        params.set("dir", state.sort.dir);
      }
    }
    // Secim varsa ayri uc kullanilir: `keys` yalnizca o anahtarlari yazar.
    const onlyPicked = picked.length > 0 && pickedInput.checked;
    if (onlyPicked) params.set("keys", picked.join(","));
    const file = onlyPicked ? "export-selected.xlsx" : "export.xlsx";
    // Indirme normal bir baglanti gibi gider; Content-Disposition adi belirler.
    const link = h("a", {
      href: `/api/groups/${state.activeId}/${file}?${params.toString()}`,
      download: true,
    });
    document.body.appendChild(link);
    link.click();
    link.remove();
    closeModal();
  };

  openModal("Excel'e aktar", body, [
    { label: "Vazgeç", onClick: closeModal },
    { label: "İndir", kind: "primary", onClick: start },
  ]);
}

// --- Görevlerim: kişisel kanban -----------------------------------------

const TASK_LABEL = { todo: "Yapılacak", doing: "Yapılıyor", done: "Yapıldı" };
const TASK_ORDER = ["todo", "doing", "done"];
// Son tarih rozetinin baslik metni; renk CSS'te due-<durum> ile gelir.
const DUE_TITLE = {
  overdue: "Gecikti",
  today: "Bugün son gün",
  soon: "Yaklaşıyor",
  later: "Son tarih",
  none: "",
};
const KEY_SUGGEST_MS = 220;

/** "2026-09-11" -> "11.09.2026". Saat dilimi kaydirmasi olmasin diye elle. */
function dateText(iso) {
  const parts = String(iso || "").split("-");
  if (parts.length !== 3) return String(iso || "");
  return `${parts[2]}.${parts[1]}.${parts[0]}`;
}

function showTasks() {
  // Sefer ekrani aciksa once o kapanir: ikisi de ayni alanda durur.
  if (typeof leaveCampaign === "function") leaveCampaign();
  state.view = "tasks";
  el("placeholder").hidden = true;
  el("group-view").hidden = true;
  el("tasks-view").hidden = false;
  el("tasks-entry").classList.add("active");
  renderGroups();
  closeDrawer();
  loadTasks();
  saveView();
}

function leaveTasks() {
  state.view = "groups";
  el("tasks-view").hidden = true;
  el("tasks-entry").classList.remove("active");
}

function setTaskBadge(summary) {
  const counts = summary || { open: 0, overdue: 0 };
  state.tasks.summary = counts;
  const badge = el("tasks-badge");
  badge.textContent = String(counts.open || 0);
  badge.classList.toggle("overdue", (counts.overdue || 0) > 0);
  badge.title = counts.overdue ? `${counts.overdue} gecikmiş görev` : "Açık görev sayısı";
}

async function refreshTaskBadge() {
  try {
    setTaskBadge(await api("/api/tasks/summary"));
  } catch (err) {
    // Rozet ikincil bilgi; okunamazsa ekran yine calisir.
  }
}

async function loadTasks() {
  const params = new URLSearchParams();
  if (state.tasks.query) params.set("q", state.tasks.query);
  if (state.tasks.includeOld) params.set("include_old_done", "1");
  const query = params.toString();
  try {
    const data = await api(`/api/tasks${query ? "?" + query : ""}`);
    state.tasks.columns = data.columns || [];
    state.tasks.oldDone = data.old_done_count || 0;
    state.tasks.baseUrl = data.base_url || "";
    setTaskBadge(data.summary);
    renderKanban();
  } catch (err) {
    fail(err);
  }
}

function renderKanban() {
  const board = el("kanban");
  clear(board);
  const shown = state.tasks.columns.reduce((total, column) => total + column.count, 0);
  el("tasks-count").textContent = `${shown} görev`;

  const old = el("tasks-old");
  old.hidden = !state.tasks.oldDone && !state.tasks.includeOld;
  old.textContent = state.tasks.includeOld ? "Eskileri gizle" : "Eskileri göster";
  old.title = state.tasks.includeOld
    ? "30 günden eski biten görevleri gizle"
    : `${state.tasks.oldDone} eski biten görev gizli`;
  el("tasks-hint").textContent =
    !state.tasks.includeOld && state.tasks.oldDone
      ? `${state.tasks.oldDone} eski biten görev gizli`
      : "";

  state.tasks.columns.forEach((column) => board.appendChild(renderColumn(column)));
}

function renderColumn(column) {
  const body = h("div", { class: "kanban-body" }, []);
  if (!column.tasks.length) {
    body.appendChild(h("div", { class: "kanban-drop", text: "Buraya sürükle" }));
  }
  column.tasks.forEach((task) => body.appendChild(taskCard(task, column)));

  body.addEventListener("dragover", (event) => {
    if (state.tasks.drag === null) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = "move";
    body.classList.add("drop-over");
  });
  body.addEventListener("dragleave", (event) => {
    if (event.target === body) body.classList.remove("drop-over");
  });
  body.addEventListener("drop", (event) => {
    event.preventDefault();
    body.classList.remove("drop-over");
    const carried = Number(event.dataTransfer.getData("text/plain"));
    const taskId = carried || state.tasks.drag;
    if (!taskId) return;
    moveTask(taskId, column.status, dropIndex(body, event.clientY, taskId));
  });

  return h("div", { class: "kanban-column column-" + column.status }, [
    h("div", { class: "kanban-head" }, [
      h("span", { class: "kanban-name", text: column.label }),
      h("span", { class: "count", text: String(column.count) }),
    ]),
    body,
  ]);
}

/** Birakilan noktanin sutun icindeki sirasi (surüklenen kart sayilmaz). */
function dropIndex(body, y, taskId) {
  const cards = Array.from(body.querySelectorAll(".task-card")).filter(
    (card) => Number(card.dataset.id) !== taskId
  );
  let index = 0;
  cards.forEach((card) => {
    const box = card.getBoundingClientRect();
    if (y > box.top + box.height / 2) index += 1;
  });
  return index;
}

function taskCard(task, column) {
  const card = h("div", {
    class: "task-card" + (task.status === "done" ? " is-done" : ""),
    draggable: "true",
    tabindex: "0",
    onclick: () => taskModal(task),
  });
  card.dataset.id = String(task.id);

  const index = TASK_ORDER.indexOf(column.status);
  const arrows = h("div", { class: "task-arrows" }, [
    h("button", {
      text: "←",
      title: index > 0 ? `${TASK_LABEL[TASK_ORDER[index - 1]]} sütununa taşı` : "En soldaki sütun",
      disabled: index <= 0,
      onclick: (event) => {
        event.stopPropagation();
        moveTask(task.id, TASK_ORDER[index - 1], null);
      },
    }),
    h("button", {
      text: "→",
      title:
        index < TASK_ORDER.length - 1
          ? `${TASK_LABEL[TASK_ORDER[index + 1]]} sütununa taşı`
          : "En sağdaki sütun",
      disabled: index >= TASK_ORDER.length - 1,
      onclick: (event) => {
        event.stopPropagation();
        moveTask(task.id, TASK_ORDER[index + 1], null);
      },
    }),
  ]);

  card.appendChild(
    h("div", { class: "task-top" }, [h("span", { class: "task-title", text: task.title }), arrows])
  );

  if (task.due_date) {
    card.appendChild(
      h("div", { class: "task-line" }, [
        h("span", {
          class: "due-badge due-" + (task.due_state || "none"),
          text: dateText(task.due_date),
          title: DUE_TITLE[task.due_state] || "Son tarih",
        }),
      ])
    );
  }

  if (task.source === "mail") {
    card.classList.add("is-mail");
    card.appendChild(taskMailLine(task));
  }

  if (task.issue) card.appendChild(taskIssueLine(task.issue));

  if (task.description) {
    card.appendChild(metinCiz(h("div", { class: "task-desc" }, []), task.description));
  }
  if (task.son_durum) card.appendChild(taskStatusLine(task));
  if (task.status === "done" && task.done_at) {
    card.appendChild(h("div", { class: "task-done-at", text: "Bitti: " + stamp(task.done_at) }));
  }

  card.addEventListener("dragstart", (event) => {
    state.tasks.drag = task.id;
    event.dataTransfer.effectAllowed = "move";
    event.dataTransfer.setData("text/plain", String(task.id));
    card.classList.add("dragging");
  });
  card.addEventListener("dragend", () => {
    state.tasks.drag = null;
    card.classList.remove("dragging");
  });
  return card;
}

/** Kartin son durum satiri: metnin ILK satiri + tarih, tiklayinca defter acilir.
 *
 * Kart dar: tam metin gorev penceresinde durur, burada yalnizca "nerede
 * kaldi" sorusunun bir satirlik cevabi gorunur.
 */
function taskStatusLine(task) {
  const ilk = String(task.son_durum || "").split("\n")[0];
  const line = h("button", {
    class: "task-status",
    type: "button",
    title: "Son durum geçmişi",
    onclick: (event) => {
      event.stopPropagation();
      openTaskHistory(event.currentTarget, task);
    },
  }, [icon("clock")]);
  if (task.son_durum_at) {
    line.appendChild(h("span", { class: "when", text: shortStamp(task.son_durum_at) }));
  }
  line.appendChild(metinCiz(h("span", { class: "task-status-text" }, []), ilk));
  return line;
}

/** E-postadan gelen kartin kaynak satiri: zarf, rozet, gonderen, mesaj sayisi. */
function taskMailLine(task) {
  const line = h("div", { class: "task-mail" }, [icon("mail")]);
  line.appendChild(h("span", { class: "mail-badge", text: "e-posta" }));
  if (task.mail_sender) {
    line.appendChild(h("span", { class: "mail-from", text: "Kimden: " + task.mail_sender }));
  }
  if ((task.mail_count || 0) > 1) {
    const last = shortStamp(task.mail_last_at);
    line.appendChild(
      h("span", {
        class: "mail-count",
        text: `${task.mail_count} mesaj${last ? " · son: " + last : ""}`,
        title: "Aynı konuşmadan gelen mesaj sayısı",
      })
    );
  }
  return line;
}

/** "GG.AA SS:dd" -- kart dar, yil yazilmaz. */
function shortStamp(iso) {
  const full = stamp(iso);
  if (!full) return "";
  const parts = full.split(" ");
  const day = (parts[0] || "").split(".");
  return day.length === 3 ? `${day[0]}.${day[1]} ${parts[1] || ""}`.trim() : full;
}

function taskIssueLine(issue) {
  const line = h("div", { class: "task-issue" }, [
    h("button", {
      class: "task-key",
      text: issue.key,
      title: "Kaydın detayını aç",
      onclick: (event) => {
        event.stopPropagation();
        openDrawer(issue.key);
      },
    }),
  ]);
  if (issue.url) {
    line.appendChild(
      h("a", {
        class: "task-jira",
        href: issue.url,
        target: "_blank",
        rel: "noopener",
        text: "Jira",
        title: "Jira'da aç",
        onclick: (event) => event.stopPropagation(),
      })
    );
  }
  line.appendChild(
    h(
      "button",
      {
        class: "task-ask",
        title: "Son durumu sor: Teams'te mesajı hazırla",
        onclick: (event) => {
          event.stopPropagation();
          askStatus(issue.key);
        },
      },
      [icon("chat")]
    )
  );
  if (issue.summary) {
    line.appendChild(h("span", { class: "task-summary", text: issue.summary }));
  }
  if (issue.status_text) {
    line.appendChild(
      h("span", {
        class:
          "status-pill" + (issue.status_category ? " status-" + issue.status_category : ""),
        text: issue.status_text,
      })
    );
  } else if (!issue.fetched) {
    line.appendChild(h("span", { class: "task-unfetched", text: "henüz çekilmedi" }));
  }
  return line;
}

async function moveTask(taskId, status, position) {
  try {
    const payload = { status: status };
    if (position !== null && position !== undefined) payload.position = position;
    await api(`/api/tasks/${taskId}/move`, { method: "POST", body: JSON.stringify(payload) });
    await loadTasks();
    // Gorev kapatmak XP verir: Sefer rozeti aninda dogru sayiyi gostersin.
    if (typeof refreshCampaignBadge === "function") refreshCampaignBadge();
  } catch (err) {
    fail(err);
  }
}

/** Pencerede e-posta kaynagi: salt okunur bilgi + Outlook'ta acma dugmesi. */
function mailSourceBox(task) {
  const box = h("div", { class: "mail-source" }, [
    h("div", { class: "mail-source-head" }, [
      icon("mail"),
      h("strong", { text: "E-postadan geldi" }),
    ]),
  ]);
  if (task.mail_sender) {
    box.appendChild(h("div", { class: "hint", text: "Kimden: " + task.mail_sender }));
  }
  if (task.mail_received_at) {
    box.appendChild(h("div", { class: "hint", text: "Alındı: " + stamp(task.mail_received_at) }));
  }
  if ((task.mail_count || 0) > 1) {
    box.appendChild(
      h("div", {
        class: "hint",
        text: `${task.mail_count} mesaj · son: ${stamp(task.mail_last_at)}`,
      })
    );
  }
  box.appendChild(
    h("p", {
      class: "hint",
      text: "Kaynak bilgisi düzenlenemez. Aynı konuşmadan ikinci bir görev üretilmez.",
    })
  );
  box.appendChild(
    h("button", {
      text: "Outlook'ta aç",
      title: "E-postayı Outlook penceresinde aç",
      onclick: () => openTaskMail(task),
    })
  );
  return box;
}

async function openTaskMail(task) {
  try {
    await api(`/api/tasks/${task.id}/open-mail`, { method: "POST" });
    toast("Outlook açıldı", task.title, "ok");
  } catch (err) {
    fail(err);
  }
}

/** Panodaki "E-postayı tara" dugmesi. */
async function scanMailNow() {
  const button = el("tasks-scan");
  button.disabled = true;
  try {
    const data = await api("/api/mail/scan", { method: "POST" });
    const summary = data.summary || {};
    const items = (summary.errors || []).map((item) =>
      h("span", { text: item.message || item.code })
    );
    toast(
      "E-posta tarandı",
      `${summary.created || 0} yeni görev · ${summary.appended || 0} mesaj eklendi · ` +
        `${summary.scanned || 0} e-posta tarandı`,
      items.length ? "" : "ok",
      items
    );
    await loadTasks();
  } catch (err) {
    fail(err);
  } finally {
    button.disabled = false;
  }
}

/** Yeni görev / düzenleme penceresi. `preset` grid'den gelen ön dolu alanlar. */
function taskModal(existing, preset) {
  const seed = existing || preset || {};
  const titleInput = h("input", { type: "text", value: seed.title || "" });
  const descInput = h("textarea", { placeholder: "Görev ne hakkında?" });
  descInput.value = seed.description || "";
  const sonInput = h("textarea", { placeholder: "Nerede kaldı?", rows: "3" });
  sonInput.value = seed.son_durum || "";
  const noteInput = h("textarea", { placeholder: "Kendine not" });
  noteInput.value = seed.note || "";
  // "Düzelt" dugmesi alani sarmalayan bir kutuya girer; pencereye o kutu
  // konur, alanin kendisi degil (bkz. duzelt.js).
  const descBox = duzeltKutusu(descInput, "Açıklama");
  const sonBox = duzeltKutusu(sonInput, "Son durum");
  const noteBox = duzeltKutusu(noteInput, "Not");

  // "Son durum" her degistiginde deftere yazilir; etiketin yaninda defteri
  // acan bag durur (kartin son durum satiriyla AYNI popover).
  const sonLabel = h("label", { text: "Son durum" });
  if (existing && existing.son_durum_changes) {
    sonLabel.appendChild(
      h("button", {
        class: "history-link",
        type: "button",
        text: `Geçmiş (${existing.son_durum_changes})`,
        onclick: (event) => {
          event.preventDefault();
          openTaskHistory(event.currentTarget, existing);
        },
      })
    );
  }
  const sonAlan = h("div", { class: "field" }, [sonLabel, sonBox]);
  const dueInput = h("input", { type: "date", value: seed.due_date || "" });

  const statusSelect = h("select", {}, []);
  TASK_ORDER.forEach((name) =>
    statusSelect.appendChild(h("option", { value: name, text: TASK_LABEL[name] }))
  );
  statusSelect.value = seed.status || "todo";

  const keyInput = h("input", {
    type: "text",
    value: seed.issue_key || "",
    placeholder: "DEMO-1",
    autocomplete: "off",
  });
  const suggestions = h("datalist", { id: "task-issue-keys" }, []);
  keyInput.setAttribute("list", "task-issue-keys");
  const keyNote = h("p", { class: "hint", text: "" });

  let suggestTimer = null;
  const suggest = async () => {
    const typed = keyInput.value.trim();
    try {
      const data = await api(`/api/issues/keys?q=${encodeURIComponent(typed)}`);
      clear(suggestions);
      (data.keys || []).forEach((item) =>
        suggestions.appendChild(
          h("option", { value: item.key, label: item.summary || item.key })
        )
      );
      if (!typed) {
        keyNote.textContent = "";
        return;
      }
      const found = (data.keys || []).find(
        (item) => item.key === typed.toUpperCase() && item.fetched
      );
      keyNote.textContent = found
        ? found.summary || ""
        : "Bu kayıt henüz çekilmedi; bağ yine de kurulur.";
    } catch (err) {
      keyNote.textContent = "";
    }
  };
  keyInput.addEventListener("input", () => {
    clearTimeout(suggestTimer);
    suggestTimer = setTimeout(suggest, KEY_SUGGEST_MS);
  });
  suggest();

  const body = h("div", {}, [
    field("Ad", titleInput),
    field("Açıklama", descBox),
    sonAlan,
    field("Not", noteBox),
    h("div", { class: "row" }, [field("Son tarih", dueInput), field("Durum", statusSelect)]),
    field("Jira kaydı", h("div", {}, [keyInput, suggestions, keyNote])),
    h("p", {
      class: "hint",
      text: "Görevler Jira'ya gitmez; bağlı kayıt yalnızca burada görünür. Ctrl+Enter kaydeder.",
    }),
  ]);

  // Kaynak alanlari bilgi amaclidir, duzenlenemez.
  if (existing && existing.source === "mail") body.appendChild(mailSourceBox(existing));

  const save = async () => {
    const payload = {
      title: titleInput.value,
      description: descInput.value,
      son_durum: sonInput.value,
      note: noteInput.value,
      due_date: dueInput.value,
      status: statusSelect.value,
      issue_key: keyInput.value,
    };
    try {
      if (existing) {
        await api(`/api/tasks/${existing.id}`, { method: "PUT", body: JSON.stringify(payload) });
      } else {
        await api("/api/tasks", { method: "POST", body: JSON.stringify(payload) });
      }
      closeModal();
      if (state.view === "tasks") {
        await loadTasks();
        return;
      }
      // Grid'den açılan pencere kullanıcıyı panoya sürüklemez; rozet tazelenir.
      await refreshTaskBadge();
      toast(
        existing ? "Görev güncellendi" : "Görev oluşturuldu",
        titleInput.value.trim(),
        "ok"
      );
    } catch (err) {
      fail(err);
    }
  };

  body.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
      event.preventDefault();
      save();
    }
  });

  const buttons = [{ label: "Vazgeç", onClick: closeModal }];
  if (existing) {
    buttons.unshift({ label: "Sil", kind: "danger", onClick: () => dropTask(existing) });
  }
  buttons.push({ label: "Kaydet", kind: "primary", onClick: save });

  openModal(existing ? "Görevi düzenle" : "Yeni görev", body, buttons);
}

async function dropTask(task) {
  if (!confirm(`"${task.title}" görevi silinsin mi?`)) return;
  try {
    await api(`/api/tasks/${task.id}`, { method: "DELETE" });
    closeModal();
    await loadTasks();
  } catch (err) {
    fail(err);
  }
}

/** Grid satirindan görev: anahtar bagli, ad Jira özetiyle ön dolu. */
function taskFromRow(row) {
  const index = state.columns.findIndex((column) => column.id === "summary");
  const summary = index >= 0 ? (row.cells[index] || {}).text : "";
  taskModal(null, { issue_key: row.key, title: summary || row.key });
}

function exportTasks() {
  const params = new URLSearchParams();
  params.set("status", "all");
  if (state.tasks.query) params.set("q", state.tasks.query);
  const link = h("a", { href: `/api/tasks/export.xlsx?${params.toString()}`, download: true });
  document.body.appendChild(link);
  link.click();
  link.remove();
}

// --- Guncelle -----------------------------------------------------------

/** Halkayi doldurur: 0..1 arasi oran saat yonunde sari yay olur. */
function setRingProgress(ratio) {
  const ring = el("progress-ring");
  if (!ring) return;
  ring.style.strokeDasharray = RING_LENGTH.toFixed(2);
  ring.style.strokeDashoffset = (RING_LENGTH * (1 - ratio)).toFixed(2);
}

async function startRefresh(groupId) {
  // Onceki isin yesil parlamasi surerken yeni is baslarsa halka sifirlanir.
  clearTimeout(state.doneTimer);
  el("progress-ring-box").classList.remove("is-done");
  setRingProgress(0);
  try {
    const payload = groupId ? { group_id: groupId } : {};
    const data = await api("/api/refresh", { method: "POST", body: JSON.stringify(payload) });
    applyRefreshStatus(data.status);
    schedulePoll();
  } catch (err) {
    fail(err);
  }
}

function schedulePoll() {
  clearTimeout(state.pollTimer);
  state.pollTimer = setTimeout(pollRefresh, POLL_MS);
}

async function pollRefresh() {
  try {
    const data = await api("/api/refresh/status");
    applyRefreshStatus(data.status);
    if (data.status.running) schedulePoll();
  } catch (err) {
    // Sunucu kapanmis olabilir; sessizce birak.
  }
}

function applyRefreshStatus(status) {
  const running = status.running;
  el("progress").hidden = !running;
  el("refresh-cancel").hidden = !running;
  el("refresh-all").disabled = running;
  el("refresh-group").disabled = running;
  el("progress-stage").textContent = status.stage || "";
  const total = status.total || 0;
  const done = status.done || 0;
  el("progress-count").textContent = total ? `${done}/${total}` : "";
  setRingProgress(total ? Math.min(1, done / total) : 0);

  if (running) {
    if (state.lastRefreshState !== "running") Starfield.hyperspace(true);
    state.lastRefreshState = "running";
    return;
  }
  if (state.lastRefreshState !== "running") {
    state.lastRefreshState = status.state;
    return;
  }
  state.lastRefreshState = status.state;
  finishRefresh(status);
}

function finishRefresh(status) {
  Starfield.hyperspace(false);
  const clean = status.state !== "error" && status.state !== "cancelled";
  if (clean) {
    // Halka bir buçuk saniye yeşil parlar, sonra özet balonuna geçilir.
    el("progress").hidden = false;
    el("progress-ring-box").classList.add("is-done");
    setRingProgress(1);
    clearTimeout(state.doneTimer);
    state.doneTimer = setTimeout(() => {
      el("progress-ring-box").classList.remove("is-done");
      el("progress").hidden = true;
      refreshToast(status);
    }, DONE_MS);
  } else {
    refreshToast(status);
  }

  state.changed = status.changed || {};
  clearTimeout(state.changedTimer);
  state.changedTimer = setTimeout(() => {
    state.changed = {};
  }, CHANGED_MS);
  loadGroups(state.activeId).catch(fail);
  if (state.view === "tasks") loadTasks();
  else refreshTaskBadge();
  // Guncelle puan uretmis olabilir (filodan dusen kayit, durum gecisi).
  if (typeof refreshCampaignBadge === "function") refreshCampaignBadge();
}

function refreshToast(status) {
  if (status.state === "error") {
    const message = (status.error && status.error.message) || "Bilinmeyen hata";
    toast("Güncelleme başarısız", message, "error");
  } else if (status.state === "cancelled") {
    toast("Güncelleme iptal edildi", "İptale kadar çekilenler kaydedildi.");
  } else {
    const summary = status.summary || {};
    const parts = [
      `${summary.fetched || 0} çekildi`,
      `${summary.new || 0} yeni`,
      `${summary.updated || 0} güncellendi`,
    ];
    const mail = summary.mail;
    if (mail && mail.created) parts.push(`${mail.created} görev e-postadan`);
    const items = [];
    (mail && mail.errors ? mail.errors : []).forEach((item) =>
      items.push(h("span", { text: "E-posta: " + (item.message || item.code) }))
    );
    (status.errors || []).forEach((item) =>
      items.push(h("span", { text: `${item.name}: ${item.message}` }))
    );
    const missing = summary.not_found || [];
    if (missing.length) {
      const line = h("span", { text: "Bulunamayan: " });
      missing.forEach((key, index) => {
        if (index) line.appendChild(document.createTextNode(", "));
        line.appendChild(
          h("a", {
            class: "key-link",
            href: state.baseUrl ? `${state.baseUrl}/browse/${key}` : "#",
            target: "_blank",
            rel: "noopener",
            text: key,
          })
        );
      });
      items.push(line);
      items.push(h("span", { class: "toast-sub", text: "Bilinen galakside bulunamadı." }));
    }
    const kind = status.errors && status.errors.length ? "" : "ok";
    toast("Güncelleme bitti", parts.join(" · "), kind, items);
  }
}

async function cancelRefresh() {
  try {
    await api("/api/refresh/cancel", { method: "POST" });
    schedulePoll();
  } catch (err) {
    fail(err);
  }
}

// --- acilis (opening crawl) ---------------------------------------------

/** Ilk acilista bir kez gosterilir; hareket kapaliysa hic gosterilmez. */
function maybeOpenCrawl(settings) {
  if (reducedMotion()) return;
  if (settings["ui.motion"] === "0") return;
  if (settings["ui.crawl_seen"] === "1") return;
  const box = el("crawl");
  box.hidden = false;
  document.documentElement.classList.add("crawl-open");
  box.addEventListener("click", (event) => {
    // Onay kutusuna tiklamak acilisi kapatmaz.
    if (event.target.closest("#crawl-remember-box")) return;
    closeCrawl();
  });
  clearTimeout(state.crawlTimer);
  state.crawlTimer = setTimeout(closeCrawl, CRAWL_MS);
}

function closeCrawl() {
  const box = el("crawl");
  if (box.hidden) return;
  clearTimeout(state.crawlTimer);
  box.hidden = true;
  document.documentElement.classList.remove("crawl-open");
  if (el("crawl-remember").checked) {
    saveSetting("ui.crawl_seen", "1").catch(() => {});
  }
}

// --- baslangic ----------------------------------------------------------

function bindEvents() {
  el("new-group").addEventListener("click", () => groupModal(null));
  el("edit-group").addEventListener("click", () => groupModal(state.group));
  el("delete-group").addEventListener("click", deleteGroup);
  el("add-items").addEventListener("click", addItemsModal);
  el("empty-add").addEventListener("click", addItemsModal);
  el("choose-columns").addEventListener("click", columnsModal);
  el("export-xlsx").addEventListener("click", exportModal);
  el("mail-send").addEventListener("click", () => {
    // Pencere ayri dosyada (mailsend.js); dosya yuklenmediyse sessiz kalinir.
    if (typeof mailSendModal === "function") mailSendModal();
  });
  el("local-fields").addEventListener("click", localFieldsModal);
  el("tasks-entry").addEventListener("click", showTasks);
  el("tasks-entry").addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      showTasks();
    }
  });
  el("new-task").addEventListener("click", () => taskModal(null));
  el("tasks-export").addEventListener("click", exportTasks);
  el("tasks-scan").addEventListener("click", scanMailNow);
  el("tasks-old").addEventListener("click", () => {
    state.tasks.includeOld = !state.tasks.includeOld;
    loadTasks();
  });
  el("task-search").addEventListener("input", (event) => {
    state.tasks.query = event.target.value;
    clearTimeout(state.searchTimer);
    state.searchTimer = setTimeout(loadTasks, 200);
  });
  el("history-close").addEventListener("click", closeHistory);
  el("history-clear").addEventListener("click", clearHistory);
  el("refresh-all").addEventListener("click", () => startRefresh(null));
  el("refresh-group").addEventListener("click", () => startRefresh(state.activeId));
  el("refresh-cancel").addEventListener("click", cancelRefresh);
  el("modal-close").addEventListener("click", closeModal);
  el("drawer-close").addEventListener("click", closeDrawer);
  el("drawer-fields").addEventListener("click", drawerFieldsModal);
  el("drawer-empty").addEventListener("click", () => {
    state.drawerShowEmpty = !state.drawerShowEmpty;
    el("drawer-empty").setAttribute("aria-checked", String(state.drawerShowEmpty));
    renderDrawerBody();
  });

  el("modal").addEventListener("click", (event) => {
    if (event.target === el("modal")) closeModal();
  });

  el("search").addEventListener("input", (event) => {
    state.query = event.target.value;
    clearTimeout(state.searchTimer);
    state.searchTimer = setTimeout(loadIssues, 200);
  });

  // Popover disina tiklayinca kapanir; kendi icindeki tiklama gecerli kalir.
  document.addEventListener("mousedown", (event) => {
    const box = el("history-popover");
    if (box.hidden || box.contains(event.target)) return;
    if (event.target.closest && event.target.closest(".clock")) return;
    closeHistory();
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      if (!el("crawl").hidden) closeCrawl();
      else if (!el("history-popover").hidden) closeHistory();
      else if (!el("modal").hidden) closeModal();
      else if (!el("drawer").hidden) closeDrawer();
      return;
    }
    const active = document.activeElement;
    const typing = active && /^(INPUT|TEXTAREA|SELECT)$/.test(active.tagName);
    if (event.key === "/" && !typing) {
      if (!el("tasks-view").hidden) {
        event.preventDefault();
        el("task-search").focus();
      } else if (!el("group-view").hidden) {
        event.preventDefault();
        el("search").focus();
      }
    }
  });
}

document.addEventListener("DOMContentLoaded", () => {
  bindShell();
  bindEvents();

  api("/api/health")
    .then((data) => {
      el("version").textContent = "s" + data.version;
    })
    .catch(() => {});

  api("/api/settings")
    .then((data) => {
      const settings = data.settings || {};
      applyAppearance(settings);
      maybeOpenCrawl(settings);
      state.baseUrl = (settings["jira.base_url"] || "").replace(/\/+$/, "");
      // "Düzelt" dugmesi: ozellik acik mi, Copilot ayarlanmis mi.
      if (typeof duzeltAyarla === "function") duzeltAyarla(settings);
      // Dugme yalnizca ozellik acikken ve Windows'ta gorunur.
      state.tasks.mail = settings["mail.enabled"] === "1" && settings.mail_supported !== false;
      el("tasks-scan").hidden = !state.tasks.mail;
      // E-posta gonderimi de Outlook COM ister: Windows disinda dugme pasif.
      state.mailSupported = settings.mail_supported !== false;
      state.mailMode = settings["mailsend.mode"] === "send" ? "send" : "display";
      if (!state.mailSupported) {
        const button = el("mail-send");
        button.disabled = true;
        button.title = "Bu özellik yalnız Windows'ta Outlook ile çalışır.";
      }
      const hint = el("connection-hint");
      if (settings["jira.base_url"] && settings.secret_set) {
        hint.textContent = "Bağlantı hazır: " + settings["jira.base_url"];
      } else {
        hint.innerHTML =
          'Önce <a href="/settings">Ayarlar</a> ekranından Jira bağlantısını tanımlayın.';
      }
    })
    .catch(() => {});

  // Son gorunum: hash/localStorage'daki filo/gorevlerim/sefer'e donulur.
  const startupView = resolveStartupView();
  if (startupView.view === "tasks" || startupView.view === "campaign") {
    state.view = startupView.view;
  }
  loadGroups(startupView.groupId || undefined)
    .then(() => {
      if (startupView.view === "tasks") showTasks();
      else if (startupView.view === "campaign") showCampaign();
    })
    .catch(fail);
  refreshTaskBadge();
  pollRefresh();
});
