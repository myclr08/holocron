// Teams Aramalar: istatistik seridi, liste/kisiler sekmeleri, kisi cekmecesi.
// app.js buyudugu icin ayri dosya; ortak yardimcilar (api, h, el, toast)
// app.js ve common.js icinden gelir, cerceve ve CDN yok.

const CALL_WINDOWS = [7, 30, 90];
const CALL_SEARCH_MS = 250;

// Uc dilimin cizim sirasi ve renk sinifi (CSS'te .split-<kind>).
const CALL_SPLIT_ORDER = ["meeting", "group_call", "one_to_one"];

const callsState = {
  days: 30,
  query: "",
  tab: "list",
  calls: [],
  people: [],
  stats: null,
  scannedAt: "",
  supported: true,
  peopleSort: { key: "ms", dir: "desc" },
  searchTimer: null,
  drawerId: null,
  // Ayni anda yalnizca en son istegin yaniti cizilir.
  request: 0,
  unmatched: 0,
};

// --- gorunum acma / kapama ----------------------------------------------

function showCalls() {
  if (typeof leaveTasks === "function") leaveTasks();
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
    callsState.scannedAt = data.scanned_at || "";
    callsState.supported = data.supported !== false;
    callsState.stats = data.stats || null;
    callsState.unmatched = data.unmatched || 0;
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
        (result.meetings_matched ? ` · ${result.meetings_matched} toplantı eşleşti` : "") +
        (result.recurring_matched ? ` (${result.recurring_matched} tekrarlayan)` : ""),
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

function renderCalls() {
  el("calls-count").textContent = `${callsState.calls.length} arama`;
  // Eslesmeyenler rozeti yalnizca SUPHELI olanlari sayar: grup sohbetinden
  // baslatilan aramalarin takvimde karsiligi zaten beklenmez. Sayi listenin
  // kendi yanitindan gelir; ayri bir istek ATILMAZ.
  const orphans = callsState.unmatched;
  el("calls-unmatched").hidden = orphans === 0;
  el("calls-unmatched-count").textContent = String(orphans);
  el("calls-unmatched").title = `${orphans} arama toplantıyla eşleşmedi (grup sohbetleri sayılmaz)`;
  el("calls-hint").textContent = callsHint();
  el("calls-scan").disabled = !callsState.supported;
  el("calls-tab-list").classList.toggle("on", callsState.tab === "list");
  el("calls-tab-people").classList.toggle("on", callsState.tab === "people");
  el("calls-tab-list").setAttribute("aria-selected", String(callsState.tab === "list"));
  el("calls-tab-people").setAttribute("aria-selected", String(callsState.tab === "people"));
  renderCallStats();
  if (callsState.tab === "people") renderCallPeople();
  else renderCallList();
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

  // 1. En cok gorusulen bes kisi (yalniz birebir aramalar).
  const top = (stats.top || []).length
    ? stats.top.map((person) =>
        statRow(person.name, `${person.duration_text} · ${person.count}`, () =>
          openCallPerson(person.counterpart_id, person.name)
        )
      )
    : [h("p", { class: "stat-note", text: "Bu pencerede birebir görüşme yok." })];
  strip.appendChild(statCard("En çok görüşülen", top));

  // 2. Uc dilim: toplanti / grup / birebir.
  const bar = h("div", { class: "split-bar" }, []);
  const legend = [];
  CALL_SPLIT_ORDER.forEach((kind) => {
    const part = (stats.split || []).find((item) => item.kind === kind);
    if (!part) return;
    if (part.percent > 0) {
      bar.appendChild(h("span", { class: "split-" + kind, style: `width:${part.percent}%` }));
    }
    legend.push(statRow(part.label, `%${part.percent} · ${part.hours} sa`));
  });
  strip.appendChild(statCard("Dağılım", [bar, ...legend]));

  // 3. Aradim / arandim; kacirilan ve reddedilen ayri satirda.
  const direction = stats.direction || {};
  const out = direction.outgoing || { count: 0, duration_text: "—" };
  const incoming = direction.incoming || { count: 0, duration_text: "—" };
  strip.appendChild(
    statCard("Aradım / Arandım", [
      statRow("Aradım", `${out.count} · ${out.duration_text}`),
      statRow("Arandım", `${incoming.count} · ${incoming.duration_text}`),
      h("p", {
        class: "stat-note",
        text: `Kaçırılan ${direction.missed || 0} · reddedilen ${direction.declined || 0}`,
      }),
    ])
  );

  // 4. Toplam temas suresi.
  strip.appendChild(
    statCard("Toplam temas", [
      h("div", { class: "stat-figure", text: stats.total_text || "—" }),
      h("p", {
        class: "stat-note",
        text: `${stats.connected || 0} görüşme · ${stats.calls || 0} arama`,
      }),
    ])
  );

  // 5. Is gunu basina ortalama + en yogun gun.
  const workday = stats.workday || {};
  const busiest = workday.busiest;
  strip.appendChild(
    statCard("İş günü başına", [
      h("div", { class: "stat-figure", text: workday.average_text || "—" }),
      h("p", {
        class: "stat-note",
        text: busiest
          ? `En yoğun gün ${dateText(busiest.date)} · ${busiest.duration_text}`
          : `${workday.days || 0} iş günü (Pzt-Cum)`,
      }),
    ])
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
  const kinds = [h("span", { class: "call-kind kind-" + call.kind, text: call.kind_label })];
  if (call.source === "chat") {
    // Bu kayit arama gecmisinde degil, toplanti sohbetinde bulundu.
    kinds.push(
      h("span", {
        class: "call-source",
        text: "sohbetten",
        title: "Toplantı sohbetindeki katılım kaydından",
      })
    );
  }
  row.appendChild(h("td", {}, kinds));
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

// --- teshis: neden eslesmedi? -------------------------------------------

const UNMATCHED_REASONS = {
  no_thread_id: "Aramada toplantı kimliği yok",
  no_calendar_with_core: "Bu kimlik takvimde bulunamadı",
  matches_now: "Yeniden çekilince eşleşecek",
  group_chat_thread: "Bu bir grup sohbeti araması, takvimde karşılığı beklenmez",
};

function reasonText(reason) {
  const marker = String(reason || "");
  if (marker.startsWith("only_time_gap:")) {
    return `Takvimde en yakın kayıt ${marker.split(":")[1]} dk uzakta`;
  }
  return UNMATCHED_REASONS[marker] || marker;
}

async function openUnmatched() {
  const body = openCallsDrawer("Eşleşmeyen aramalar", "Teşhis");
  clear(body);
  body.appendChild(h("p", { class: "hint", text: "Takvim okunuyor..." }));
  try {
    const data = await api("/api/calls/unmatched?days=" + callsState.days);
    renderUnmatched(body, data);
  } catch (err) {
    clear(body);
    body.appendChild(h("p", { class: "hint", text: err.message }));
  }
}

function renderUnmatched(body, data) {
  clear(body);
  const summary = data.summary || {};
  const lines = [
    ["Eşleşmeyen", String(summary.unmatched || 0)],
    ["Bakılması gereken", String(summary.suspicious || 0)],
    ["Grup sohbeti", String(summary.group_chat || 0)],
    ["Kimliği yok", String(summary.no_thread_id || 0)],
    ["Takvimde yok", String(summary.core_not_in_calendar || 0)],
    ["Yeniden çekince düzelir", String(summary.matched_after_fix || 0)],
    ["Takvim kaydı", String(data.calendar_events || 0)],
  ];
  lines.forEach(([label, value]) =>
    body.appendChild(
      h("div", { class: "detail-row" }, [
        h("div", { class: "label", text: label }),
        h("div", { class: "value", text: value }),
      ])
    )
  );

  if (!(data.calls || []).length) {
    body.appendChild(h("p", { class: "hint", text: "Eşleşmeyen arama yok." }));
    return;
  }

  body.appendChild(h("h3", { text: "Aramalar" }));
  data.calls.forEach((call) => {
    const box = h("div", { class: "unmatched" }, [
      h("div", { class: "unmatched-head" }, [
        h("span", { class: "when", text: stamp(call.started_at) }),
        h("span", { class: "what", text: call.title }),
        h("span", { class: "much", text: call.duration_text }),
      ]),
      h("div", {
        class: "unmatched-why" + (call.thread_kind === "group_chat" ? " is-fine" : ""),
        text: reasonText(call.reason),
      }),
    ]);
    if (call.thread_core) {
      box.appendChild(h("div", { class: "unmatched-id", text: "kimlik: " + call.thread_core }));
    }
    if ((call.participants || []).length) {
      box.appendChild(
        h("div", { class: "unmatched-id", text: "katılanlar: " + call.participants.join(", ") })
      );
    }
    (call.candidates || []).forEach((item) =>
      box.appendChild(
        h("div", { class: "unmatched-candidate" }, [
          h("span", { class: "when", text: stamp(item.start_time) }),
          h("span", { class: "what", text: item.subject || "(konusuz)" }),
          h("span", { class: "much", text: `${item.event_type || "?"} · ${item.gap_minutes} dk` }),
        ])
      )
    );
    body.appendChild(box);
  });
}

// --- katilim teshisi ----------------------------------------------------

const ATTENDANCE_DECISIONS = {
  created: "Kayıt oluştu",
  "skipped:no_me": "Katılımcı listesinde yokum",
  "skipped:no_duration": "Süre yazmıyor",
  "skipped:started": "Yalnızca başlama mesajı",
  "deduped:history": "Arama geçmişinde zaten var",
};

function decisionText(decision) {
  const marker = String(decision || "");
  if (marker.startsWith("merged:")) return "Aynı toplantıya eklendi";
  return ATTENDANCE_DECISIONS[marker] || marker;
}

function decisionClass(decision) {
  const marker = String(decision || "");
  if (marker === "created") return "is-good";
  if (marker.startsWith("merged:")) return "is-fine";
  return "is-skip";
}

async function openAttendance() {
  const body = openCallsDrawer("Katılım teşhisi", "Teşhis");
  clear(body);
  body.appendChild(h("p", { class: "hint", text: "Toplantı sohbetleri okunuyor..." }));
  try {
    const data = await api("/api/calls/attendance-diagnose?days=" + callsState.days);
    renderAttendance(body, data);
  } catch (err) {
    clear(body);
    body.appendChild(h("p", { class: "hint", text: err.message }));
  }
}

function renderAttendance(body, data) {
  clear(body);
  const summary = data.summary || {};
  const lines = [
    ["Mesaj", String(data.messages || 0)],
    ["Kayıt oluşan", String(data.created || 0)],
    ["Atlanan", String(summary.skipped || 0)],
    ["Birleşen", String(summary.merged || 0)],
    ["Kimliğim", data.my_mri_known ? "bulundu" : "bulunamadı"],
  ];
  lines.forEach(([label, value]) =>
    body.appendChild(
      h("div", { class: "detail-row" }, [
        h("div", { class: "label", text: label }),
        h("div", { class: "value", text: value }),
      ])
    )
  );

  if (!(data.meetings || []).length) {
    body.appendChild(h("p", { class: "hint", text: "Bu pencerede katılım mesajı yok." }));
    return;
  }

  body.appendChild(h("h3", { text: "Toplantılar" }));
  data.meetings.forEach((item) => {
    const box = h("div", { class: "unmatched" }, [
      h("div", { class: "unmatched-head" }, [
        h("span", { class: "when", text: stamp(item.ended_at) }),
        h("span", { class: "what", text: item.subject || item.thread_core || "(konusuz)" }),
        h("span", { class: "much", text: item.my_seconds ? `${Math.round(item.my_seconds / 60)} dk` : "—" }),
      ]),
      h("div", {
        class: "unmatched-why " + decisionClass(item.decision),
        text: decisionText(item.decision),
      }),
      h("div", {
        class: "unmatched-id",
        text:
          `tür: ${item.event_kind} · katılımcı: ${item.part_count}` +
          ` · ben: ${item.me_present || "yok"}` +
          ` · callid: ${item.has_call_id ? "var" : "yok"}` +
          ` · icaluid: ${item.has_ical_uid ? "var" : "yok"}` +
          (item.day ? ` · gün: ${item.day}` : ""),
      }),
    ]);
    body.appendChild(box);
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

/** Kisi cekmecesi: ozet + o kisiyle tum gorusmeler + ortak grup/toplantilar. */
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

  body.appendChild(h("h3", { text: "Grup ve toplantılar" }));
  body.appendChild(
    callListBox(data.group_calls || [], "Ortak grup araması ya da toplantı yok.")
  );
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

/** Toplanti / grup / tek arama ayrintisi. */
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
  if (call.topic && call.topic !== call.title) lines.push(["Sohbet", call.topic]);
  if (call.source === "chat") lines.push(["Kaynak", "Toplantı sohbetindeki katılım kaydı"]);
  if (call.meeting_organizer) lines.push(["Organizatör", call.meeting_organizer]);
  if (call.my_response) lines.push(["Yanıtım", call.my_response]);
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

  if ((call.attendees || []).length) {
    // Davetliler: takvim kaydindan. Katilanlarla ayni sey DEGIL.
    body.appendChild(h("h3", { text: "Davetliler" }));
    const guests = h("div", { class: "call-list" }, []);
    call.attendees.forEach((name) =>
      guests.appendChild(
        h("div", { class: "call-line" }, [h("span", { class: "what", text: name })])
      )
    );
    body.appendChild(guests);
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
  el("calls-unmatched").addEventListener("click", openUnmatched);
  el("calls-attendance").addEventListener("click", openAttendance);
  el("calls-drawer-close").addEventListener("click", closeCallsDrawer);
  el("calls-tab-list").addEventListener("click", () => {
    callsState.tab = "list";
    renderCalls();
  });
  el("calls-tab-people").addEventListener("click", () => {
    callsState.tab = "people";
    renderCalls();
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
