// Teams Aramalar: istatistik seridi, liste/kisiler/gruplar sekmeleri,
// kisi cekmecesi. app.js buyudugu icin ayri dosya; ortak yardimcilar
// (api, h, el, toast) app.js ve common.js icinden gelir, cerceve ve CDN yok.

const CALL_WINDOWS = [7, 30, 90];
const CALL_SEARCH_MS = 250;

// Dagilim dilimlerinin cizim sirasi ve renk sinifi (CSS'te .split-<kind>).
const CALL_SPLIT_ORDER = ["one_to_one", "group_call"];

const callsState = {
  days: 30,
  query: "",
  tab: "list",
  calls: [],
  people: [],
  groups: [],
  stats: null,
  scannedAt: "",
  supported: true,
  peopleSort: { key: "ms", dir: "desc" },
  groupSort: { key: "ms", dir: "desc" },
  searchTimer: null,
  drawerId: null,
  // Ayni anda yalnizca en son istegin yaniti cizilir.
  request: 0,
  // Gruplar sekmesinden secilen grup: liste yalnizca onun aramalarini gosterir.
  group: null,
};

// --- gorunum acma / kapama ----------------------------------------------

function showCalls() {
  if (typeof leaveTasks === "function") leaveTasks();
  if (typeof leaveCampaign === "function") leaveCampaign();
  state.view = "calls";
  el("placeholder").hidden = true;
  el("group-view").hidden = true;
  el("calls-view").hidden = false;
  el("calls-entry").classList.add("active");
  if (typeof renderGroups === "function") renderGroups();
  if (typeof closeDrawer === "function") closeDrawer();
  renderCallWindows();
  loadCalls();
}

function leaveCalls() {
  el("calls-view").hidden = true;
  el("calls-entry").classList.remove("active");
  closeCallsDrawer();
  if (state.view === "calls") state.view = "groups";
}

// --- veri ----------------------------------------------------------------

function callParams(extra) {
  const params = new URLSearchParams({ days: String(callsState.days) });
  if (callsState.query) params.set("q", callsState.query);
  if (callsState.group) params.set("group", callsState.group.key);
  Object.entries(extra || {}).forEach(([key, value]) => params.set(key, value));
  return params;
}

/** Pencere ve arama degisiminde TEK istek: liste, kisiler ve istatistik
 * ayni yanittan gelir. (Iki ayri istek, her tusa basista butun tabloyu iki
 * kez okutuyordu.) */
async function loadCalls() {
  const token = ++callsState.request;
  try {
    const data = await api("/api/calls/view?" + callParams().toString());
    // Gecikmis yanit taze olani ezmesin.
    if (token !== callsState.request) return;
    callsState.calls = data.calls || [];
    callsState.people = data.people || [];
    callsState.groups = data.groups || [];
    callsState.scannedAt = data.scanned_at || "";
    callsState.supported = data.supported !== false;
    callsState.stats = data.stats || null;
    setCallsBadge(data.count || 0);
    renderCalls();
  } catch (err) {
    fail(err);
  }
}

/** Kenar cubugu rozeti: pencere icindeki arama sayisi.
 *
 * Ekran acilmadan bir kez cagrilir; istatistik ucu yalnizca sayilari doner,
 * satirlari cizmez. */
async function refreshCallsBadge() {
  try {
    const stats = await api("/api/calls/stats?days=" + callsState.days);
    setCallsBadge(stats.calls || 0);
  } catch (err) {
    // Rozet ikincil bilgi; okunamazsa ekran yine calisir.
  }
}

function setCallsBadge(count) {
  const badge = el("calls-badge");
  badge.textContent = String(count || 0);
  badge.title = `Son ${callsState.days} günde ${count || 0} arama`;
}

async function scanCalls() {
  const button = el("calls-scan");
  button.disabled = true;
  el("calls-hint").textContent = "Teams önbelleği okunuyor...";
  try {
    const result = await api("/api/calls/scan", { method: "POST" });
    const newest = result.latest_call_at ? shortStamp(result.latest_call_at) : "—";
    const notes = [];
    if (result.warning) {
      // Kilitli kalan yazma gunlugu: en yeni aramalar eksik olabilir.
      notes.push(h("div", { class: "toast-sub", text: result.warning }));
    }
    if (result.skipped) {
      notes.push(
        h("div", {
          class: "toast-sub",
          text: `Kopyalanan ${result.copied} dosya · atlanan ${result.skipped}` +
            kindSummary(result.skipped_kinds),
        })
      );
    }
    if (result.read_ms) {
      notes.push(
        h("div", {
          class: "toast-sub",
          text: `${result.databases || 0} veritabanı okundu · ${(result.read_ms / 1000).toFixed(1)} sn`,
        })
      );
    }
    toast(
      "Aramalar çekildi",
      `${result.scanned} kayıt, en yeni: ${newest}` +
        ` · ${result.new} yeni · ${result.updated} güncellendi` +
        (result.groups ? ` · ${result.groups} grup araması` : ""),
      result.warning ? "error" : "ok",
      notes
    );
    await loadCalls();
  } catch (err) {
    fail(err);
  } finally {
    button.disabled = false;
    el("calls-hint").textContent = callsHint();
  }
}

/** Atlanan dosyalarin tur dokumu: " (log 1, lock 1)". */
function kindSummary(kinds) {
  const parts = Object.entries(kinds || {}).map(([name, count]) => `${name} ${count}`);
  return parts.length ? " (" + parts.join(", ") + ")" : "";
}

function callsHint() {
  if (!callsState.supported) return "Bu özellik yalnız Windows'ta yeni Teams ile çalışır.";
  if (!callsState.scannedAt) return "Henüz çekilmedi: Aramaları çek düğmesine basın.";
  return "Son çekilme: " + stamp(callsState.scannedAt);
}

function exportCalls() {
  window.location.href = "/api/calls/export.xlsx?" + callParams().toString();
}

// --- ust cubuk -----------------------------------------------------------

function renderCallWindows() {
  const box = el("calls-window");
  clear(box);
  CALL_WINDOWS.forEach((days) => {
    box.appendChild(
      h("button", {
        class: "small" + (days === callsState.days ? " on" : ""),
        text: days + " gün",
        title: `Son ${days} günü göster`,
        onclick: () => {
          callsState.days = days;
          renderCallWindows();
          loadCalls();
        },
      })
    );
  });
}

// --- ana cizim -----------------------------------------------------------

const CALL_TABS = { list: "calls-tab-list", people: "calls-tab-people", groups: "calls-tab-groups" };

function renderCalls() {
  el("calls-count").textContent = `${callsState.calls.length} arama`;
  el("calls-hint").textContent = callsHint();
  el("calls-scan").disabled = !callsState.supported;
  Object.entries(CALL_TABS).forEach(([name, id]) => {
    el(id).classList.toggle("on", callsState.tab === name);
    el(id).setAttribute("aria-selected", String(callsState.tab === name));
  });
  renderCallFilter();
  renderCallStats();
  if (callsState.tab === "people") renderCallPeople();
  else if (callsState.tab === "groups") renderCallGroups();
  else renderCallList();
}

/** Secili grup serdi: "Grup: Proje ekibi ✕". */
function renderCallFilter() {
  const box = el("calls-filter");
  box.hidden = !callsState.group;
  if (callsState.group) {
    el("calls-filter-text").textContent = "Grup: " + callsState.group.name;
  }
}

/** Bir gruba tiklandi: liste sekmesi yalnizca o grubun aramalarini gosterir. */
function filterByGroup(group) {
  callsState.group = { key: group.key, name: group.name };
  callsState.tab = "list";
  loadCalls();
}

function clearCallGroupFilter() {
  callsState.group = null;
  loadCalls();
}

// --- istatistik seridi ---------------------------------------------------

function statCard(title, children) {
  return h("div", { class: "stat-card" }, [h("h3", { text: title }), ...children]);
}

function statRow(label, value, onClick) {
  const attrs = { class: "stat-row" + (onClick ? " is-link" : "") };
  if (onClick) {
    attrs.onclick = onClick;
    attrs.tabindex = "0";
    attrs.role = "button";
    attrs.onkeydown = (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        onClick();
      }
    };
  }
  return h("div", attrs, [
    h("span", { class: "who", text: label }),
    h("span", { class: "much", text: value }),
  ]);
}

function renderCallStats() {
  const strip = el("calls-stats");
  clear(strip);
  const stats = callsState.stats;
  if (!stats) return;

  // 1. Birebir aramalarda en cok gorusulen bes kisi.
  strip.appendChild(
    statCard(
      "En çok görüşülenler",
      peopleRows(stats.top, "Bu pencerede birebir görüşme yok.")
    )
  );

  // 2. Grup aramalarina katilanlar: aramanin suresi katilan herkese yazilir.
  strip.appendChild(
    statCard(
      "Grupta en çok görüşülenler",
      peopleRows(stats.group_top, "Bu pencerede grup araması yok.")
    )
  );

  // 3. Birebir + grup toplami.
  strip.appendChild(
    statCard(
      "Toplamda en çok görüşülenler",
      peopleRows(stats.combined_top, "Bu pencerede görüşme yok.")
    )
  );

  // 4. Dagilim: birebir / grup payi (adet ve sure).
  const bar = h("div", { class: "split-bar" }, []);
  const legend = [];
  CALL_SPLIT_ORDER.forEach((kind) => {
    const part = (stats.split || []).find((item) => item.kind === kind);
    if (!part) return;
    if (part.percent > 0) {
      bar.appendChild(h("span", { class: "split-" + kind, style: `width:${part.percent}%` }));
    }
    legend.push(statRow(part.label, `${part.count} arama · ${part.duration_text}`));
    legend.push(
      h("p", { class: "stat-note", text: `süre %${part.percent} · adet %${part.count_percent}` })
    );
  });
  strip.appendChild(statCard("Dağılım", [bar, ...legend]));
}

/** Istatistik kutusundaki kisa kisi listesi; tiklanan kisi cekmeceyi acar. */
function peopleRows(people, emptyText) {
  if (!(people || []).length) return [h("p", { class: "stat-note", text: emptyText })];
  return people.map((person) =>
    statRow(person.name, `${person.duration_text} · ${person.count}`, () =>
      openCallPerson(person.counterpart_id, person.name)
    )
  );
}

// --- liste sekmesi -------------------------------------------------------

const CALL_LIST_HEADS = ["Tarih", "Yön", "Karşı taraf", "Tür", "Durum", "Süre"];

function renderCallList() {
  const head = el("calls-head");
  const body = el("calls-body");
  clear(head);
  clear(body);
  CALL_LIST_HEADS.forEach((text) => head.appendChild(h("th", { text })));

  el("calls-empty").hidden = callsState.calls.length > 0;
  el("calls-empty-text").textContent = callsState.scannedAt
    ? "Bu pencerede arama yok."
    : "Henüz arama çekilmedi.";
  el("calls-table").hidden = !callsState.calls.length;

  callsState.calls.forEach((call) => body.appendChild(callRow(call)));
}

function callRow(call) {
  const outgoing = call.direction === "Outgoing";
  const row = h("tr", {
    class: "call-row",
    onclick: () => openCallDetail(call),
  });
  row.appendChild(h("td", { class: "when", text: stamp(call.started_at) }));
  row.appendChild(
    h("td", {}, [
      h("span", {
        class: "call-dir " + (outgoing ? "out" : "in"),
        text: outgoing ? "↗" : "↙",
        title: call.direction_label,
      }),
    ])
  );

  const name = h("td", { class: "call-title" }, [
    h("button", {
      class: "call-person",
      text: call.title,
      title: call.counterpart_id ? "Bu kişinin görüşmeleri" : "Ayrıntı",
      onclick: (event) => {
        event.stopPropagation();
        if (call.kind === "one_to_one" && call.counterpart_id) {
          openCallPerson(call.counterpart_id, call.counterpart_label || call.title);
        } else {
          openCallDetail(call);
        }
      },
    }),
  ]);
  row.appendChild(name);
  row.appendChild(
    h("td", {}, [h("span", { class: "call-kind kind-" + call.kind, text: call.kind_label })])
  );
  row.appendChild(
    h("td", {}, [
      h("span", { class: "call-state state-" + (call.state || "none"), text: call.state_label || "—" }),
    ])
  );
  row.appendChild(h("td", { class: "when", text: call.duration_text }));
  return row;
}

// --- kisiler sekmesi -----------------------------------------------------

const CALL_PEOPLE_HEADS = [
  { key: "name", label: "Kişi" },
  { key: "count", label: "Görüşme" },
  { key: "ms", label: "Süre" },
  { key: "outgoing", label: "Giden" },
  { key: "incoming", label: "Gelen" },
  { key: "missed", label: "Kaçırılan" },
];

function sortedCallPeople() {
  const { key, dir } = callsState.peopleSort;
  const people = callsState.people.slice();
  people.sort((left, right) => {
    const a = left[key];
    const b = right[key];
    const result =
      typeof a === "string" || typeof b === "string"
        ? String(a || "").localeCompare(String(b || ""), "tr")
        : (a || 0) - (b || 0);
    return dir === "asc" ? result : -result;
  });
  return people;
}

function renderCallPeople() {
  const head = el("calls-head");
  const body = el("calls-body");
  clear(head);
  clear(body);

  CALL_PEOPLE_HEADS.forEach((column) => {
    head.appendChild(
      h("th", {
        class: callsState.peopleSort.key === column.key ? "sorted" : "",
        text: column.label,
        title: "Sırala",
        onclick: () => {
          const same = callsState.peopleSort.key === column.key;
          callsState.peopleSort = {
            key: column.key,
            dir: same && callsState.peopleSort.dir === "desc" ? "asc" : "desc",
          };
          renderCallPeople();
        },
      })
    );
  });

  const people = sortedCallPeople();
  el("calls-empty").hidden = people.length > 0;
  el("calls-empty-text").textContent = "Bu pencerede birebir görüşme yok.";
  el("calls-table").hidden = !people.length;

  people.forEach((person) => {
    const row = h("tr", {
      class: "call-row",
      onclick: () => openCallPerson(person.counterpart_id, person.name),
    });
    row.appendChild(h("td", { text: person.name }));
    row.appendChild(h("td", { class: "when", text: String(person.count) }));
    row.appendChild(h("td", { class: "when", text: person.duration_text }));
    row.appendChild(h("td", { class: "when", text: String(person.outgoing) }));
    row.appendChild(h("td", { class: "when", text: String(person.incoming) }));
    row.appendChild(h("td", { class: "when", text: String(person.missed) }));
    body.appendChild(row);
  });
}

// --- gruplar sekmesi -----------------------------------------------------

const CALL_GROUP_HEADS = [
  { key: "name", label: "Grup" },
  { key: "count", label: "Arama" },
  { key: "ms", label: "Süre" },
  { key: "last_at", label: "Son arama" },
  { key: "people_count", label: "Kişi" },
];

function sortedCallGroups() {
  const { key, dir } = callsState.groupSort;
  const groups = callsState.groups.slice();
  groups.sort((left, right) => {
    const a = left[key];
    const b = right[key];
    const result =
      typeof a === "string" || typeof b === "string"
        ? String(a || "").localeCompare(String(b || ""), "tr")
        : (a || 0) - (b || 0);
    return dir === "asc" ? result : -result;
  });
  return groups;
}

function renderCallGroups() {
  const head = el("calls-head");
  const body = el("calls-body");
  clear(head);
  clear(body);

  CALL_GROUP_HEADS.forEach((column) => {
    head.appendChild(
      h("th", {
        class: callsState.groupSort.key === column.key ? "sorted" : "",
        text: column.label,
        title: "Sırala",
        onclick: () => {
          const same = callsState.groupSort.key === column.key;
          callsState.groupSort = {
            key: column.key,
            dir: same && callsState.groupSort.dir === "desc" ? "asc" : "desc",
          };
          renderCallGroups();
        },
      })
    );
  });

  const groups = sortedCallGroups();
  el("calls-empty").hidden = groups.length > 0;
  el("calls-empty-text").textContent = "Bu pencerede grup araması yok.";
  el("calls-table").hidden = !groups.length;

  groups.forEach((group) => {
    // Gruba tiklamak listeyi o grupla suzer: ayri bir ekran gerekmiyor.
    const everyone = group.participants.join(", ");
    const row = h("tr", {
      class: "call-row",
      title: everyone,
      onclick: () => filterByGroup(group),
    });
    // Etiket en fazla uc ad tasir; kalanlar varsa tam liste altta yazilir
    // (ipucunda her zaman durur).
    const cell = [h("div", { text: group.name })];
    if (group.name !== everyone) cell.push(h("div", { class: "call-people", text: everyone }));
    row.appendChild(h("td", {}, cell));
    row.appendChild(h("td", { class: "when", text: String(group.count) }));
    row.appendChild(h("td", { class: "when", text: group.duration_text }));
    row.appendChild(h("td", { class: "when", text: group.last_at ? stamp(group.last_at) : "—" }));
    row.appendChild(h("td", { class: "when", text: String(group.people_count) }));
    body.appendChild(row);
  });
}

// --- cekmece -------------------------------------------------------------

function openCallsDrawer(title, kindLabel) {
  el("calls-drawer-title").textContent = title;
  const badge = el("calls-drawer-kind");
  badge.textContent = kindLabel || "";
  badge.hidden = !kindLabel;
  el("calls-drawer").hidden = false;
  return el("calls-drawer-body");
}

function closeCallsDrawer() {
  el("calls-drawer").hidden = true;
  callsState.drawerId = null;
}

/** Kisi cekmecesi: ozet + o kisiyle tum gorusmeler + ortak grup aramalari. */
async function openCallPerson(counterpartId, name) {
  if (!counterpartId) return;
  callsState.drawerId = counterpartId;
  const body = openCallsDrawer(name || "Kişi", "Kişi");
  clear(body);
  body.appendChild(h("p", { class: "hint", text: "Okunuyor..." }));
  try {
    const data = await api(
      `/api/calls/person/${encodeURIComponent(counterpartId)}?days=${callsState.days}`
    );
    if (callsState.drawerId !== counterpartId) return;
    el("calls-drawer-title").textContent = data.person.name;
    renderCallPersonBody(body, data);
  } catch (err) {
    clear(body);
    body.appendChild(h("p", { class: "hint", text: err.message }));
  }
}

function renderCallPersonBody(body, data) {
  clear(body);
  const summary = data.summary || {};
  const lines = [
    ["Toplam süre", summary.total_text || "—"],
    ["Görüşme", `${summary.count || 0} arama`],
    ["Giden / gelen", `${summary.outgoing || 0} / ${summary.incoming || 0}`],
    ["Kaçırılan", String(summary.missed || 0)],
    ["En uzun", summary.longest_text || "—"],
    ["Grup araması", `${summary.group_count || 0} · ${summary.group_text || "—"}`],
    ["Son görüşme", summary.last_at ? stamp(summary.last_at) : "—"],
  ];
  const box = h("div", { class: "call-summary" }, []);
  lines.forEach(([label, value]) =>
    box.appendChild(
      h("div", { class: "detail-row" }, [
        h("div", { class: "label", text: label }),
        h("div", { class: "value", text: value }),
      ])
    )
  );
  body.appendChild(box);

  body.appendChild(h("h3", { text: "Görüşmeler" }));
  body.appendChild(callListBox(data.calls || [], "Bu pencerede birebir görüşme yok."));

  body.appendChild(h("h3", { text: "Grup aramaları" }));
  body.appendChild(callListBox(data.group_calls || [], "Ortak grup araması yok."));
}

function callListBox(calls, emptyText) {
  if (!calls.length) return h("p", { class: "hint", text: emptyText });
  const list = h("div", { class: "call-list" }, []);
  calls.forEach((call) => {
    list.appendChild(
      h("div", { class: "call-line", onclick: () => openCallDetail(call) }, [
        h("span", { class: "when", text: stamp(call.started_at) }),
        h("span", {
          class: "call-dir " + (call.direction === "Outgoing" ? "out" : "in"),
          text: call.direction === "Outgoing" ? "↗" : "↙",
          title: call.direction_label,
        }),
        h("span", { class: "what", text: call.title }),
        h("span", { class: "much", text: call.duration_text }),
      ])
    );
  });
  return list;
}

/** Grup ya da birebir aramanin ayrintisi. */
function openCallDetail(call) {
  callsState.drawerId = call.call_id;
  const body = openCallsDrawer(call.title, call.kind_label);
  clear(body);
  const lines = [
    ["Tarih", stamp(call.started_at)],
    ["Yön", call.direction_label || "—"],
    ["Durum", call.state_label || "—"],
    ["Süre", call.duration_text],
  ];
  // Cok kisili aramada "karsi taraf" diye bir sey yok; katilimcilar asagida.
  if (call.kind === "one_to_one" && call.counterpart_label) {
    lines.push(["Karşı taraf", call.counterpart_label]);
  }
  if (call.forwarded) lines.push(["Yönlendirme", call.forwarded]);
  lines.forEach(([label, value]) =>
    body.appendChild(
      h("div", { class: "detail-row" }, [
        h("div", { class: "label", text: label }),
        h("div", { class: "value", text: value }),
      ])
    )
  );

  if ((call.participants || []).length) {
    // Katilanlar: aramanin kendi katilimci listesi.
    body.appendChild(h("h3", { text: "Katılanlar" }));
    const list = h("div", { class: "call-list" }, []);
    const labels = call.participant_names || [];
    call.participants.forEach((person, index) => {
      // Ekranda ad durur, tiklama kimlikle gider: ham kimlik gorunmez.
      const label = labels[index] || person;
      list.appendChild(
        h("div", { class: "call-line", onclick: () => openCallPerson(person, label) }, [
          h("span", { class: "what", text: label }),
        ])
      );
    });
    body.appendChild(list);
  } else if (call.kind !== "one_to_one") {
    body.appendChild(h("p", { class: "hint", text: "Katılımcı listesi kayıtta yok." }));
  }

  if (call.kind === "group_call" && call.group_key) {
    body.appendChild(
      h("button", {
        class: "small",
        text: "Bu grubun aramaları",
        onclick: () => {
          closeCallsDrawer();
          filterByGroup({ key: call.group_key, name: call.title });
        },
      })
    );
  }

  if (call.counterpart_id && call.kind === "one_to_one") {
    body.appendChild(
      h("button", {
        class: "small",
        text: "Bu kişinin tüm görüşmeleri",
        onclick: () => openCallPerson(call.counterpart_id, call.counterpart_label),
      })
    );
  }
}

// --- baglama -------------------------------------------------------------

function bindCalls() {
  el("calls-entry").addEventListener("click", showCalls);
  el("calls-entry").addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      showCalls();
    }
  });
  el("calls-scan").addEventListener("click", scanCalls);
  el("calls-export").addEventListener("click", exportCalls);
  el("calls-drawer-close").addEventListener("click", closeCallsDrawer);
  el("calls-filter-clear").addEventListener("click", clearCallGroupFilter);
  Object.entries(CALL_TABS).forEach(([name, id]) => {
    el(id).addEventListener("click", () => {
      callsState.tab = name;
      renderCalls();
    });
  });
  el("calls-search").addEventListener("input", (event) => {
    callsState.query = event.target.value;
    clearTimeout(callsState.searchTimer);
    callsState.searchTimer = setTimeout(loadCalls, CALL_SEARCH_MS);
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !el("calls-drawer").hidden) {
      closeCallsDrawer();
      return;
    }
    const active = document.activeElement;
    const typing = active && /^(INPUT|TEXTAREA|SELECT)$/.test(active.tagName);
    if (event.key === "/" && !typing && !el("calls-view").hidden) {
      event.preventDefault();
      el("calls-search").focus();
    }
  });
}

document.addEventListener("DOMContentLoaded", () => {
  bindCalls();
  renderCallWindows();
  refreshCallsBadge();
});
