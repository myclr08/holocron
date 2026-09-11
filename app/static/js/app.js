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
const CRAWL_MS = 9600;
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
        class: "group-item" + (group.id === state.activeId ? " active" : ""),
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
  state.activeId = null;
  state.group = null;
  el("placeholder").hidden = false;
  el("group-view").hidden = true;
}

async function selectGroup(groupId, keepView) {
  const changing = state.activeId !== groupId;
  state.activeId = groupId;
  if (changing || !keepView) {
    state.query = "";
    el("search").value = "";
    state.sort = null;
  }
  el("placeholder").hidden = true;
  el("group-view").hidden = false;
  renderGroups();
  await loadIssues();
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
  state.drawerKey = key;
  el("drawer-key").textContent = key;
  const body = el("drawer-body");
  clear(body);
  body.appendChild(h("p", { class: "hint", text: "Okunuyor..." }));
  el("drawer").hidden = false;
  renderGrid();

  try {
    const data = await api(`/api/issues/${encodeURIComponent(key)}`);
    const link = el("drawer-link");
    link.hidden = !data.url;
    if (data.url) link.href = data.url;
    state.drawerFields = data.fields;
    state.drawerLocal = data.local || [];
    state.drawerFetchedAt = data.fetched_at;
    renderDrawerBody();
  } catch (err) {
    state.drawerFields = [];
    state.drawerLocal = [];
    clear(body);
    body.appendChild(h("p", { class: "hint", text: err.message }));
    el("drawer-link").hidden = true;
  }
}

function renderDrawerBody() {
  const body = el("drawer-body");
  clear(body);
  if (state.drawerFetchedAt) {
    body.appendChild(h("p", { class: "hint", text: "Çekilme: " + stamp(state.drawerFetchedAt) }));
  } else {
    body.appendChild(
      h("p", { class: "hint", text: "Bu kayıt henüz Jira'dan çekilmedi." })
    );
  }
  renderDrawerLocal(body);
  (state.drawerFields || [])
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
  td.appendChild(h("span", { class: "local-text", text: cell.text || "—" }));

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
function localEditor(field, value, hooks) {
  let node;
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
    commit();
  });

  return {
    node: node,
    focus: () => {
      node.focus();
      if (node.select) node.select();
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

function renderHistory(entries) {
  const body = el("history-body");
  clear(body);
  el("history-clear").disabled = entries.length === 0;
  if (!entries.length) {
    body.appendChild(h("p", { class: "hint", text: "Bu hücrede henüz değişim yok." }));
    return;
  }
  entries.forEach((entry) => body.appendChild(historyLine(entry)));
}

function historyLine(entry) {
  return h("div", { class: "history-line" }, [
    h("span", { class: "when", text: stamp(entry.changed_at) }),
    h("span", { class: "what" }, [
      h("span", { class: "old", text: entry.old_text || "—" }),
      document.createTextNode(" → "),
      h("span", { class: "new", text: entry.new_text || "—" }),
    ]),
    h("button", {
      class: "drop",
      text: "×",
      title: "Bu satırı sil",
      onclick: () => dropHistoryEntry(entry.id),
    }),
  ]);
}

async function dropHistoryEntry(historyId) {
  const target = state.popover;
  if (!target) return;
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
  if (!target) return;
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

function closeHistory() {
  el("history-popover").hidden = true;
  state.popover = null;
}

// --- detay cekmecesindeki yerel bolum -----------------------------------

function renderDrawerLocal(body) {
  const items = state.drawerLocal || [];
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
        h("button", {
          class: "local-open",
          text: item.text || "—",
          title: "Düzenle",
          onclick: () => edit(),
        })
      );
    };
    const edit = () => {
      clear(value);
      const editor = localEditor(item, item.value, {
        onSave: (fresh) => saveLocalValue(key, item, fresh, editor),
        onCancel: show,
      });
      value.appendChild(editor.node);
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
              h("span", { class: "old", text: entry.old_text || "—" }),
              document.createTextNode(" → "),
              h("span", { class: "new", text: entry.new_text || "—" }),
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
    // Indirme normal bir baglanti gibi gider; Content-Disposition adi belirler.
    const link = h("a", {
      href: `/api/groups/${state.activeId}/export.xlsx?${params.toString()}`,
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
    const items = [];
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
  el("local-fields").addEventListener("click", localFieldsModal);
  el("history-close").addEventListener("click", closeHistory);
  el("history-clear").addEventListener("click", clearHistory);
  el("refresh-all").addEventListener("click", () => startRefresh(null));
  el("refresh-group").addEventListener("click", () => startRefresh(state.activeId));
  el("refresh-cancel").addEventListener("click", cancelRefresh);
  el("modal-close").addEventListener("click", closeModal);
  el("drawer-close").addEventListener("click", closeDrawer);
  el("drawer-empty").addEventListener("change", (event) => {
    state.drawerShowEmpty = event.target.checked;
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
    if (event.key === "/" && !typing && !el("group-view").hidden) {
      event.preventDefault();
      el("search").focus();
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
      const hint = el("connection-hint");
      if (settings["jira.base_url"] && settings.secret_set) {
        hint.textContent = "Bağlantı hazır: " + settings["jira.base_url"];
      } else {
        hint.innerHTML =
          'Önce <a href="/settings">Ayarlar</a> ekranından Jira bağlantısını tanımlayın.';
      }
    })
    .catch(() => {});

  loadGroups().catch(fail);
  pollRefresh();
});
