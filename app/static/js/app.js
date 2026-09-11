// Ana ekran: hangar (gruplar), grid, sutun secici, detay cekmecesi, Guncelle.
// Cerceve yok, CDN yok: her sey dogrudan DOM uzerinde kurulur.

const KIND_LABEL = { manual: "Manuel", filter: "JQL filtresi" };
const SABER_COLORS = ["blue", "green", "purple", "red", "yellow", "white"];
const POLL_MS = 700;
const CHANGED_MS = 5000;

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
  drawerFetchedAt: null,
  drawerShowEmpty: false,
  lastRefreshState: "idle",
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

function toast(title, message, kind, items) {
  const box = el("toast");
  clear(box);
  box.className = "toast" + (kind ? " " + kind : "");
  box.appendChild(
    h("div", { class: "toast-head" }, [
      h("strong", { text: title }),
      h("button", { text: "✕", title: "Kapat", onclick: () => (box.hidden = true) }),
    ])
  );
  if (message) box.appendChild(h("div", { text: message }));
  if (items && items.length) {
    const list = h("ul", {}, []);
    items.forEach((item) => list.appendChild(h("li", {}, [item])));
    box.appendChild(list);
  }
  box.hidden = false;
}

function fail(err) {
  toast("Islem yapilamadi", err.message || String(err), "error");
}

// --- modal --------------------------------------------------------------

function openModal(title, body, buttons) {
  el("modal-title").textContent = title;
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
    list.appendChild(h("div", { class: "empty", text: "Henuz grup yok." }));
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
            text: "▲",
            title: "Yukari tasi",
            disabled: index === 0,
            onclick: (event) => {
              event.stopPropagation();
              move(index, -1);
            },
          }),
          h("button", {
            class: "move",
            text: "▼",
            title: "Asagi tasi",
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
  el("group-count").textContent = group.count + " kayit";
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
    const arrow = sorted ? (state.sort.dir === "asc" ? " ▲" : " ▼") : "";
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
  el("row-count").textContent =
    state.shown === state.total ? `${state.total} kayit` : `${state.shown} / ${state.total} kayit`;
}

function renderRow(row) {
  const changedFields = state.changed[row.key] || [];
  const tr = h("tr", {
    class: (row.missing ? "missing " : "") + (state.drawerKey === row.key ? "selected" : ""),
    title: row.missing ? "Bu kayit henuz Jira'dan cekilmedi." : "",
    onclick: () => openDrawer(row.key),
  });

  row.cells.forEach((cell) => {
    const changed = changedFields.indexOf(cell.field) >= 0;
    const td = h("td", {
      class: "cell-" + cell.field + (changed ? " changed" : ""),
      title: cell.text,
    });
    if (cell.field === "issuekey") {
      if (row.pinned) td.appendChild(h("span", { class: "pin-mark", text: "📌", title: "Iglenmis" }));
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
    } else {
      td.textContent = cell.text;
    }
    tr.appendChild(td);
  });

  const isFilter = state.group && state.group.kind === "filter";
  const actions = h("div", { class: "row-actions" }, [
    isFilter
      ? h("button", {
          class: row.pinned ? "on" : "",
          text: "📌",
          title: row.pinned ? "Igneyi kaldir" : "Ignele: guncellemede dusmesin",
          onclick: (event) => {
            event.stopPropagation();
            togglePin(row.key);
          },
        })
      : null,
    h("button", {
      text: "✕",
      title: "Gruptan cikar",
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
  if (!confirm(`${key} bu gruptan cikarilsin mi?`)) return;
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
    state.drawerFetchedAt = data.fetched_at;
    renderDrawerBody();
  } catch (err) {
    state.drawerFields = [];
    clear(body);
    body.appendChild(h("p", { class: "hint", text: err.message }));
    el("drawer-link").hidden = true;
  }
}

function renderDrawerBody() {
  const body = el("drawer-body");
  clear(body);
  if (state.drawerFetchedAt) {
    body.appendChild(h("p", { class: "hint", text: "Cekilme: " + state.drawerFetchedAt }));
  }
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

// --- grup olusturma / duzenleme ----------------------------------------

function groupModal(existing) {
  const nameInput = h("input", { type: "text", value: existing ? existing.name : "" });
  const jqlInput = h("textarea", { placeholder: "project = DEMO AND status = Acik" });
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
    field("Tur", kindBox),
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

  openModal(existing ? "Filoyu duzenle" : "Yeni Filo", body, [
    { label: "Vazgec", onClick: closeModal },
    { label: "Kaydet", kind: "primary", onClick: save },
  ]);
}

async function deleteGroup() {
  if (!state.group) return;
  if (!confirm(`"${state.group.name}" grubu silinsin mi? Kayitlar veritabaninda kalir.`)) return;
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
        "Anahtarlari yapistirin. Satir, virgul ya da bosluk fark etmez; " +
        "Jira baglantilarinin icinden de anahtar okunur.",
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
        `${data.already.length} zaten vardi`,
        `${data.invalid.length} anlasilmadi`,
      ];
      const extra = data.invalid.length
        ? [h("span", { text: "Anlasilmayan: " + data.invalid.join(", ") })]
        : [];
      toast("Kayit ekleme", parts.join(" · "), data.added.length ? "ok" : "", extra);
    } catch (err) {
      fail(err);
    }
  };

  openModal("Kayit ekle", body, [
    { label: "Vazgec", onClick: closeModal },
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
      chosenBox.appendChild(h("div", { class: "empty-note", text: "Sutun secilmedi." }));
    }
    chosen.forEach((id, index) => {
      chosenBox.appendChild(
        h("div", { class: "entry" }, [
          h("span", { class: "name", text: nameOf(id), title: id }),
          h("span", { class: "id", text: id }),
          h("button", {
            text: "▲",
            title: "Yukari",
            disabled: index === 0,
            onclick: () => {
              const swap = chosen[index - 1];
              chosen[index - 1] = chosen[index];
              chosen[index] = swap;
              renderChosen();
            },
          }),
          h("button", {
            text: "▼",
            title: "Asagi",
            disabled: index === chosen.length - 1,
            onclick: () => {
              const swap = chosen[index + 1];
              chosen[index + 1] = chosen[index];
              chosen[index] = swap;
              renderChosen();
            },
          }),
          h("button", {
            text: "✕",
            title: "Cikar",
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
      )
      .slice(0, 200);
    if (!list.length) {
      availableBox.appendChild(
        h("div", {
          class: "empty-note",
          text: state.fields.length
            ? "Eslesen alan yok."
            : "Alan katalogu bos. Ayarlar ekranindan cekin ya da bir kez Guncelle calistirin.",
        })
      );
    }
    list.forEach((item) => {
      availableBox.appendChild(
        h("div", { class: "entry" }, [
          h("span", { class: "name", text: item.name, title: item.id }),
          h("span", { class: "id", text: item.id }),
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
    });
  }

  search.addEventListener("input", renderAvailable);
  renderChosen();
  renderAvailable();

  const body = h("div", { class: "column-editor" }, [
    h("div", {}, [h("h3", { text: "Secili sutunlar" }), chosenBox]),
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
      toast("Sutunlar", "Genel varsayilan guncellendi.", "ok");
    } catch (err) {
      fail(err);
    }
  };

  openModal("Sutunlar", body, [
    { label: "Genel varsayilan yap", onClick: makeDefault },
    { label: "Vazgec", onClick: closeModal },
    { label: "Uygula", kind: "primary", onClick: save },
  ]);
}

// --- Guncelle -----------------------------------------------------------

async function startRefresh(groupId) {
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
  const ratio = status.total ? Math.min(100, Math.round((status.done / status.total) * 100)) : 0;
  el("progress-fill").style.width = ratio + "%";

  if (running) {
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
  if (status.state === "error") {
    const message = (status.error && status.error.message) || "Bilinmeyen hata";
    toast("Guncelleme basarisiz", message, "error");
  } else if (status.state === "cancelled") {
    toast("Guncelleme iptal edildi", "Iptale kadar cekilenler kaydedildi.");
  } else {
    const summary = status.summary || {};
    const parts = [
      `${summary.fetched || 0} cekildi`,
      `${summary.new || 0} yeni`,
      `${summary.updated || 0} guncellendi`,
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
    }
    const kind = status.errors && status.errors.length ? "" : "ok";
    toast("Guncelleme bitti", parts.join(" · "), kind, items);
  }

  state.changed = status.changed || {};
  clearTimeout(state.changedTimer);
  state.changedTimer = setTimeout(() => {
    state.changed = {};
  }, CHANGED_MS);
  loadGroups(state.activeId).catch(fail);
}

async function cancelRefresh() {
  try {
    await api("/api/refresh/cancel", { method: "POST" });
    schedulePoll();
  } catch (err) {
    fail(err);
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

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      if (!el("modal").hidden) closeModal();
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
      state.baseUrl = (settings["jira.base_url"] || "").replace(/\/+$/, "");
      const hint = el("connection-hint");
      if (settings["jira.base_url"] && settings.secret_set) {
        hint.textContent = "Baglanti hazir: " + settings["jira.base_url"];
      } else {
        hint.innerHTML =
          'Once <a href="/settings">Ayarlar</a> ekranindan Jira baglantisini tanimlayin.';
      }
    })
    .catch(() => {});

  loadGroups().catch(fail);
  pollRefresh();
});
