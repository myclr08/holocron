// Sefer paneli: kahraman şeridi, haftalık emirler, güç dengesi, rozet duvarı,
// XP defteri ve geçmiş seferler. app.js büyüdüğü için ayrı dosya; ortak
// yardımcılar (api, h, el, icon, toast) app.js ve common.js içinden gelir.
// Çerçeve yok, CDN yok.

// Pazartesi özet kartı kaç milisaniye ekranda durur.
const DIGEST_MS = 10000;
// Rütbe atlamasını anlamak için son görülen rütbe burada saklanır.
const RANK_STORE_KEY = "holocron.campaign.rank";
// Kaynak -> defter satırındaki ikon adı (common.js içindeki kendi çizimlerimiz).
const SOURCE_ICONS = {
  task: "task",
  jira: "issue",
  mail: "mail",
  teams: "chat",
  streak: "flame",
  quest: "orders",
  badge: "medal",
};

const campaignState = {
  data: null,
  source: "",
  digestTimer: null,
  request: 0,
};

// --- görünüm açma / kapama ----------------------------------------------

function showCampaign() {
  if (typeof leaveTasks === "function") leaveTasks();
  if (typeof leaveCalls === "function") leaveCalls();
  state.view = "campaign";
  el("placeholder").hidden = true;
  el("group-view").hidden = true;
  el("campaign-view").hidden = false;
  el("campaign-entry").classList.add("active");
  if (typeof renderGroups === "function") renderGroups();
  if (typeof closeDrawer === "function") closeDrawer();
  loadCampaign();
}

function leaveCampaign() {
  el("campaign-view").hidden = true;
  el("campaign-entry").classList.remove("active");
  if (state.view === "campaign") state.view = "groups";
}

// --- veri ----------------------------------------------------------------

async function loadCampaign() {
  const token = ++campaignState.request;
  try {
    const data = await api("/api/campaign");
    if (token !== campaignState.request) return;
    campaignState.data = data;
    renderCampaign();
  } catch (err) {
    fail(err);
  }
}

/** Kenar çubuğu rozeti: seferdeki toplam XP. Panel açılmadan da okunur. */
async function refreshCampaignBadge() {
  try {
    const data = await api("/api/campaign");
    campaignState.data = data;
    setCampaignBadge(data);
    if (!el("campaign-view").hidden) renderCampaign();
  } catch (err) {
    // Rozet ikincil bilgi; okunamazsa ekran yine çalışır.
  }
}

function setCampaignBadge(data) {
  const badge = el("campaign-badge");
  const campaign = (data || {}).campaign;
  badge.textContent = campaign ? String(data.xp || 0) : "—";
  badge.title = campaign
    ? `${data.rank.label} · hedef ${campaign.target_xp} XP · ${campaign.days_left} gün kaldı`
    : "Sefer yok: panelden başlatın";
}

// --- çizim ---------------------------------------------------------------

function renderCampaign() {
  const data = campaignState.data || {};
  setCampaignBadge(data);
  const running = !!data.campaign;
  el("campaign-empty").hidden = running;
  el("campaign-body").hidden = !running;
  el("campaign-end").hidden = !running;
  el("campaign-count").textContent = running
    ? `${data.xp} / ${data.campaign.target_xp} XP`
    : "sefer yok";

  renderHistory(data.history || []);
  if (!running) {
    // Sefer yokken de geçmiş kartları görünür: "neyi bitirdim" sorusu durur.
    el("campaign-digest").hidden = true;
    return;
  }

  renderHero(data);
  renderQuests(data.quests || []);
  renderWeek(data);
  renderBadgeWall(data.badges || []);
  renderLedgerFilter(data.sources || []);
  renderLedger(data.ledger || []);
  renderDigest(data.digest);
  celebrateRank(data);
}

/** Kahraman şeridi: rütbe hologramı, sefer adı, XP çubuğu, seri alevi. */
function renderHero(data) {
  const box = el("campaign-hero");
  clear(box);
  const campaign = data.campaign;

  box.appendChild(h("div", { class: "hero-rank" }, [rankArt(data.rank), h("div", {
    class: "hero-rank-label",
    text: data.rank.label,
  })]));

  const bar = h("div", { class: "xp-bar" }, [
    h("div", { class: "xp-fill" }),
  ]);
  bar.firstChild.style.width = Math.min(100, data.percent) + "%";

  const next = data.next_rank
    ? `${data.next_rank.label} için ${data.next_rank.remaining} XP`
    : "En yüksek rütbedesiniz";

  box.appendChild(
    h("div", { class: "hero-main" }, [
      h("div", { class: "hero-name", text: campaign.name }),
      h("div", { class: "hero-line" }, [
        h("span", { class: "hero-xp", text: `${data.xp} / ${campaign.target_xp} XP` }),
        h("span", { class: "hero-left", text: `${campaign.days_left} gün kaldı` }),
      ]),
      bar,
      h("div", { class: "hero-next", text: next }),
      h("div", {
        class: "hint",
        text: `Bitiş: ${dateText(campaign.ends_at)} · tarih geçince sefer kapanır, XP sıfırdan başlar`,
      }),
    ])
  );

  const streak = data.streak || {};
  box.appendChild(
    h("div", { class: "hero-streak" + (streak.days ? " burning" : "") }, [
      icon("flame"),
      h("div", { class: "streak-days", text: String(streak.days || 0) }),
      h("div", { class: "streak-label", text: "iş günü seri" }),
      h("div", {
        class: "streak-grace" + (streak.grace_used ? " used" : ""),
        text: streak.grace_text || "",
        title: "Ayda bir kaçırılan iş gününü affeder",
      }),
    ])
  );
}

/** Rütbe görseli; dosya yoksa kendi çizdiğimiz hologram yedeği gelir. */
function rankArt(rank) {
  const box = h("div", { class: "rank-art" });
  const art = h("img", { src: rank.image, alt: rank.label, draggable: "false" });
  art.addEventListener("error", () => {
    clear(box);
    box.appendChild(hologram(rank.code));
  });
  box.appendChild(art);
  return box;
}

const RANK_PIPS = { padawan: 1, knight: 2, master: 3, council: 4, legend: 5 };

/** Görsel dosyası yokken çizilen hologram: altıgen çerçeve + rütbe kadar nokta. */
function hologram(code) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("class", "holo");
  svg.setAttribute("viewBox", "0 0 80 80");
  svg.setAttribute("role", "img");
  svg.setAttribute("aria-label", "Rütbe hologramı");
  const shape = (tag, attrs) => {
    const node = document.createElementNS("http://www.w3.org/2000/svg", tag);
    Object.entries(attrs).forEach(([key, value]) => node.setAttribute(key, value));
    svg.appendChild(node);
    return node;
  };
  shape("path", { class: "holo-frame", d: "M40 6 68 22v36L40 74 12 58V22z" });
  shape("path", { class: "holo-frame-in", d: "M40 14 61 26v28L40 66 19 54V26z" });
  const pips = RANK_PIPS[code] || 1;
  for (let index = 0; index < pips; index += 1) {
    shape("circle", { class: "holo-pip", cx: String(26 + index * 7), cy: "40", r: "2.6" });
  }
  return svg;
}

/** Haftalık emirler: üç kart, ilerleme çubuğu, biten karta ✓. */
function renderQuests(quests) {
  const box = el("campaign-quests");
  clear(box);
  if (!quests.length) {
    box.appendChild(
      h("div", {
        class: "hint",
        text: "Bu hafta için emir yok: pano temiz görünüyor, yeni hafta yeniden bakılır.",
      })
    );
    return;
  }
  quests.forEach((quest) => {
    const fill = h("div", { class: "quest-fill" });
    fill.style.width = Math.min(100, quest.percent) + "%";
    box.appendChild(
      h("div", { class: "quest-card" + (quest.done ? " is-done" : "") }, [
        h("div", { class: "quest-top" }, [
          icon("orders"),
          h("span", { class: "quest-title", text: quest.title }),
          quest.done ? h("span", { class: "quest-tick", text: "✓" }) : null,
        ]),
        h("div", { class: "quest-bar" }, [fill]),
        h("div", { class: "quest-foot" }, [
          h("span", { text: `${quest.progress} / ${quest.target}` }),
          h("span", { class: "quest-points", text: `+${quest.points} XP` }),
        ]),
      ])
    );
  });
}

/** Bu haftanın sayaçları. */
function renderWeek(data) {
  const box = el("campaign-week");
  clear(box);
  const week = data.week || {};
  box.appendChild(
    h("div", { class: "week-counts" }, [
      weekCount(String(week.tasks_done || 0), "bu hafta kapanan görev"),
      weekCount(String(week.issues_left || 0), "bu hafta düşen kayıt"),
      weekCount(String(week.xp || 0), "bu hafta XP"),
    ])
  );
}

function weekCount(value, label) {
  return h("div", { class: "week-count" }, [
    h("strong", { text: value }),
    h("span", { text: label }),
  ]);
}

/** Rozet duvarı: kazanılan renkli, kazanılmayan gri hologram. */
function renderBadgeWall(badges) {
  const box = el("campaign-badges");
  clear(box);
  badges.forEach((badge) => {
    const art = h("div", { class: "badge-art" });
    const image = h("img", { src: badge.image, alt: badge.label, draggable: "false" });
    image.addEventListener("error", () => {
      clear(art);
      art.appendChild(badgeHologram(badge.code));
    });
    art.appendChild(image);
    box.appendChild(
      h("div", {
        class: "badge-tile" + (badge.earned ? " earned" : " locked"),
        title: badge.earned
          ? `${badge.hint} · kazanıldı: ${shortStamp(badge.earned_at)}`
          : badge.hint,
      }, [art, h("span", { class: "badge-name", text: badge.label })])
    );
  });
}

function badgeHologram(code) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("class", "holo");
  svg.setAttribute("viewBox", "0 0 60 60");
  svg.setAttribute("aria-hidden", "true");
  const add = (tag, attrs) => {
    const node = document.createElementNS("http://www.w3.org/2000/svg", tag);
    Object.entries(attrs).forEach(([key, value]) => node.setAttribute(key, value));
    svg.appendChild(node);
  };
  add("circle", { class: "holo-frame", cx: "30", cy: "30", r: "22" });
  add("circle", { class: "holo-frame-in", cx: "30", cy: "30", r: "15" });
  // Koda göre sabit bir açı: her rozetin kendi çizgisi olsun.
  const angle = (String(code).length * 37) % 360;
  add("path", {
    class: "holo-pip",
    d: `M30 30 L${30 + 15 * Math.cos((angle * Math.PI) / 180)} ${
      30 + 15 * Math.sin((angle * Math.PI) / 180)
    }`,
  });
  return svg;
}

// --- XP defteri ----------------------------------------------------------

function renderLedgerFilter(sources) {
  const box = el("ledger-sources");
  clear(box);
  const all = [{ id: "", label: "Tümü" }].concat(sources);
  all.forEach((source) => {
    box.appendChild(
      h("button", {
        class: campaignState.source === source.id ? "on" : "",
        text: source.label,
        onclick: () => {
          campaignState.source = source.id;
          loadLedger();
        },
      })
    );
  });
}

async function loadLedger() {
  try {
    const params = new URLSearchParams();
    if (campaignState.source) params.set("source", campaignState.source);
    const data = await api("/api/campaign/ledger?" + params.toString());
    renderLedgerFilter((campaignState.data || {}).sources || []);
    renderLedger(data.events || []);
  } catch (err) {
    fail(err);
  }
}

function renderLedger(events) {
  const box = el("campaign-ledger");
  clear(box);
  if (!events.length) {
    box.appendChild(h("div", { class: "hint", text: "Bu süzgeçte defter satırı yok." }));
    return;
  }
  events.forEach((event) => {
    box.appendChild(
      h("div", { class: "ledger-line" }, [
        h("span", { class: "when", text: shortStamp(event.at) }),
        icon(SOURCE_ICONS[event.source] || "task"),
        h("span", { class: "what", text: event.title || event.kind }),
        h("span", { class: "points", text: "+" + event.points }),
        h("button", {
          class: "line-drop",
          text: "×",
          title: "Bu XP'yi sil",
          onclick: () => dropLedgerEvent(event),
        }),
      ])
    );
  });
}

/** Defter satırını siler; sefer baştan değerlendirilir. */
async function dropLedgerEvent(event) {
  if (!confirm("Bu XP silinecek.")) return;
  try {
    const data = await api(`/api/campaign/ledger/${event.id}`, { method: "DELETE" });
    campaignState.data = data.panel;
    renderCampaign();
    const lost = (data.revoked || []).length;
    toast(
      "XP silindi",
      lost ? `${event.points} XP düştü · ${lost} rozet geri alındı` : `${event.points} XP düştü`,
      "ok"
    );
  } catch (err) {
    fail(err);
  }
}

function exportLedger() {
  const params = new URLSearchParams();
  if (campaignState.source) params.set("source", campaignState.source);
  window.location.href = "/api/campaign/ledger.xlsx?" + params.toString();
}

// --- geçmiş seferler -----------------------------------------------------

function renderHistory(history) {
  const box = el("campaign-history");
  clear(box);
  if (!history.length) {
    box.appendChild(h("div", { class: "hint", text: "Henüz biten sefer yok." }));
    return;
  }
  history.forEach((item) => {
    box.appendChild(
      h("div", { class: "history-card" }, [
        h("div", { class: "history-top" }, [
          h("div", { class: "history-name", text: item.name }),
          h("button", {
            class: "line-drop",
            text: "×",
            title: "Bu seferi sil",
            onclick: () => dropHistory(item),
          }),
        ]),
        h("div", { class: "history-when", text: `${dateText(item.starts_at)} → ${dateText(item.ends_at)}` }),
        h("div", { class: "history-xp", text: `${item.total_xp} / ${item.target_xp} XP` }),
        h("div", { class: "history-rank", text: (item.rank || {}).label || "" }),
        h("div", {
          class: "history-foot",
          text: `${(item.badges || []).length} rozet · en uzun seri ${item.streak} gün`,
        }),
      ])
    );
  });
}

/** Biten bir seferi defteri ve rozetleriyle siler. */
async function dropHistory(item) {
  if (!confirm(`"${item.name}" seferi, defteri ve rozetleriyle silinecek. Silinsin mi?`)) return;
  try {
    const data = await api(`/api/campaign/history/${item.id}`, { method: "DELETE" });
    if (campaignState.data) campaignState.data.history = data.campaigns || [];
    renderHistory(data.campaigns || []);
    toast("Sefer silindi", item.name, "ok");
  } catch (err) {
    fail(err);
  }
}

// --- pazartesi özeti ve rütbe kutlaması ----------------------------------

function renderDigest(digest) {
  const box = el("campaign-digest");
  clear(box);
  if (!digest) {
    box.hidden = true;
    return;
  }
  box.hidden = false;
  box.appendChild(
    h("div", { class: "record-head" }, [
      h("strong", { text: "Holocron kaydı" }),
      h("button", { text: "×", title: "Kapat", onclick: () => dismissDigest(digest) }),
    ])
  );
  box.appendChild(
    h("div", { class: "record-body" }, [
      h("span", { text: `Geçen hafta ${digest.xp} XP` }),
      h("span", { text: `${digest.tasks_done} görev kapandı` }),
      h("span", {
        text: digest.rank_changed
          ? `rütbe: ${(digest.rank_before || {}).label || ""} → ${(digest.rank || {}).label || ""}`
          : `rütbe: ${(digest.rank || {}).label || ""}`,
      }),
    ])
  );
  clearTimeout(campaignState.digestTimer);
  campaignState.digestTimer = setTimeout(() => dismissDigest(digest), DIGEST_MS);
}

async function dismissDigest(digest) {
  clearTimeout(campaignState.digestTimer);
  el("campaign-digest").hidden = true;
  try {
    await api("/api/campaign/digest-seen", {
      method: "POST",
      body: JSON.stringify({ week_start: digest.seen_key }),
    });
  } catch (err) {
    // Kartın bir daha çıkmaması ikincil; ekran yine çalışır.
  }
}

/** Rütbe atlandıysa kısa kutlama: yıldız alanı parlar, balon çıkar. */
function celebrateRank(data) {
  const code = (data.rank || {}).code || "";
  const key = RANK_STORE_KEY + "." + data.campaign.id;
  let previous = "";
  try {
    previous = localStorage.getItem(key) || "";
  } catch (err) {
    // Depolama kapalı: kutlama atlanır, panel yine çalışır.
    return;
  }
  try {
    localStorage.setItem(key, code);
  } catch (err) {
    return;
  }
  if (!previous || previous === code) return;
  const before = data.ranks.findIndex((rank) => rank.code === previous);
  const after = data.ranks.findIndex((rank) => rank.code === code);
  if (after <= before) return;
  el("campaign-view").classList.add("rank-up");
  setTimeout(() => el("campaign-view").classList.remove("rank-up"), 2600);
  if (typeof Starfield !== "undefined") {
    Starfield.hyperspace(true);
    setTimeout(() => Starfield.hyperspace(false), 1200);
  }
  toast("Rütbe atladınız", `Artık ${data.rank.label} sayılıyorsunuz.`, "ok");
}

// --- sefer başlatma / bitirme --------------------------------------------

async function startCampaign() {
  const payload = {
    name: el("campaign-name").value,
    ends_at: el("campaign-ends").value,
    target_xp: el("campaign-target").value,
  };
  try {
    campaignState.data = await api("/api/campaign", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    renderCampaign();
    toast("Sefer başladı", payload.name, "ok");
  } catch (err) {
    fail(err);
  }
}

async function endCampaign() {
  if (!confirm("Sefer şimdi bitirilsin mi? Yeni sefer sıfırdan başlar.")) return;
  try {
    const data = await api("/api/campaign/end", { method: "POST" });
    campaignState.data = data.panel;
    renderCampaign();
    const summary = data.ended || {};
    toast(
      "Sefer bitti",
      `${summary.total_xp || 0} XP · ${(summary.rank || {}).label || ""}`,
      "ok"
    );
  } catch (err) {
    fail(err);
  }
}

// --- bağlama -------------------------------------------------------------

function bindCampaign() {
  el("campaign-entry").addEventListener("click", showCampaign);
  el("campaign-entry").addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      showCampaign();
    }
  });
  el("campaign-start").addEventListener("click", startCampaign);
  el("campaign-end").addEventListener("click", endCampaign);
  el("ledger-export").addEventListener("click", exportLedger);
}

document.addEventListener("DOMContentLoaded", () => {
  bindCampaign();
  refreshCampaignBadge();
});
