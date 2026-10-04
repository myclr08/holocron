// Ambar: ana dala birleşmiş PR'ları PR yoluyla geri almak ("ambara al") ve
// geri getirmek ("ambardan çıkar"). Ortak yardımcılar (api, h, el, clear,
// toast, openModal) app.js ve common.js içinden gelir. Çerçeve yok, CDN yok.
//
// "Ambarda mı" bilgisi sunucuda ana dalın kendisinden türetilir; bu dosya
// yalnızca listeyi çizer, seçimi tutar ve iki düğmeyi çalıştırır.

const AMBAR_TAB_KEY = "holocron.ambar.tab";
const AMBAR_DAYS_KEY = "holocron.ambar.days";
const AMBAR_ALL = "";

const ambarState = {
  data: null,
  tab: AMBAR_ALL,
  days: 30,
  query: "",
  pick: new Set(), // "repoId:numara" -> ambara alınacak
  notes: {}, // "repoId:numara" -> not
  unpicked: new Set(), // "repoId:sha" -> ambardan çıkarılmayacak (varsayılan hepsi seçili)
  loading: false,
  request: 0,
};

function ambarRead(key, fallback) {
  try {
    const value = localStorage.getItem(key);
    return value === null ? fallback : value;
  } catch (err) {
    return fallback;
  }
}

function ambarWrite(key, value) {
  try {
    localStorage.setItem(key, value);
  } catch (err) {
    // tarayıcı depolaması kapalı olabilir; hatırlamak zorunlu değil
  }
}

// --- görünüm açma / kapama ----------------------------------------------

function showAmbar() {
  if (typeof leaveTasks === "function") leaveTasks();
  if (typeof leaveCampaign === "function") leaveCampaign();
  state.view = "ambar";
  el("placeholder").hidden = true;
  el("group-view").hidden = true;
  el("ambar-view").hidden = false;
  el("ambar-entry").classList.add("active");
  if (typeof renderGroups === "function") renderGroups();
  if (typeof closeDrawer === "function") closeDrawer();
  if (typeof saveView === "function") saveView();
  loadAmbar(true);
}

function leaveAmbar() {
  el("ambar-view").hidden = true;
  el("ambar-entry").classList.remove("active");
  if (state.view === "ambar") state.view = "groups";
}

// --- kenar çubuğu sayısı ----------------------------------------------------

function setAmbarCount(count) {
  const box = el("ambar-count");
  const value = Number(count || 0);
  box.hidden = value <= 0;
  box.textContent = value > 0 ? `· ${value}` : "";
  el("ambar-entry").title = value > 0
    ? `Ambarda ${value} PR bekliyor`
    : "Ambar: birleşmiş PR'ları PR yoluyla geri al / geri getir";
}

async function refreshAmbarCount() {
  try {
    const data = await api("/api/ambar/count");
    setAmbarCount(data.count);
  } catch (err) {
    // Sayı ikincil bilgi; okunamazsa kenar çubuğu yine çalışır.
  }
}

// --- veri ------------------------------------------------------------------

async function loadAmbar(fetchRemote) {
  const token = ++ambarState.request;
  ambarState.loading = true;
  renderAmbarHint();
  try {
    const params = new URLSearchParams({ days: String(ambarState.days), fetch: fetchRemote ? "1" : "0" });
    const data = await api("/api/ambar?" + params.toString());
    if (token !== ambarState.request) return;
    ambarState.data = data;
    setAmbarCount(data.count);
  } catch (err) {
    if (token === ambarState.request) fail(err);
  } finally {
    if (token === ambarState.request) {
      ambarState.loading = false;
      renderAmbar();
    }
  }
}

function ambarRepos() {
  return ((ambarState.data || {}).repos || []);
}

function visibleRepos() {
  const repos = ambarRepos();
  if (ambarState.tab === AMBAR_ALL) return repos;
  return repos.filter((repo) => repo.id === ambarState.tab);
}

function matches(item) {
  const query = ambarState.query.trim().toLocaleLowerCase("tr");
  if (!query) return true;
  const text = [item.number ? "#" + item.number : "", item.title, item.author, item.short]
    .concat(item.files || [])
    .join(" ")
    .toLocaleLowerCase("tr");
  return text.includes(query);
}

// --- çizim -------------------------------------------------------------------

function fmtWhen(iso) {
  if (!iso) return "";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return String(iso);
  const pad = (value) => String(value).padStart(2, "0");
  return `${pad(date.getDate())}.${pad(date.getMonth() + 1)}.${date.getFullYear()} ` +
    `${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function renderAmbarHint() {
  const hint = el("ambar-hint");
  if (ambarState.loading) {
    hint.textContent = "Depolar çekiliyor (git fetch) ve GitHub okunuyor...";
    return;
  }
  const data = ambarState.data;
  hint.textContent = data ? `Son ${data.days} gün · ${ambarRepos().length} depo` : "";
}

function renderAmbarTabs() {
  const box = el("ambar-tabs");
  clear(box);
  const repos = ambarRepos();
  if (ambarState.tab !== AMBAR_ALL && !repos.some((repo) => repo.id === ambarState.tab)) {
    ambarState.tab = AMBAR_ALL;
  }
  const tab = (id, label, count) => {
    const button = h("button", {
      type: "button",
      class: ambarState.tab === id ? "on" : "",
      role: "tab",
      "aria-selected": String(ambarState.tab === id),
      onclick: () => {
        ambarState.tab = id;
        ambarWrite(AMBAR_TAB_KEY, id);
        renderAmbar();
      },
    }, [document.createTextNode(label)]);
    if (count > 0) button.appendChild(h("span", { class: "tab-count", text: String(count) }));
    return button;
  };
  const total = repos.reduce((sum, repo) => sum + (repo.held || []).length, 0);
  box.appendChild(tab(AMBAR_ALL, "Tümü", total));
  repos.forEach((repo) => box.appendChild(tab(repo.id, repo.name, (repo.held || []).length)));
}

function renderAmbar() {
  renderAmbarHint();
  const data = ambarState.data;
  const repos = ambarRepos();
  const configured = !!data && repos.length > 0;
  el("ambar-empty").hidden = !data || configured;
  el("ambar-body").hidden = !configured;
  el("ambar-held-badge").textContent = data ? `${data.count} ambarda` : "";
  renderAmbarTabs();
  if (!configured) return;

  const shown = visibleRepos();
  const grouped = ambarState.tab === AMBAR_ALL && repos.length > 1;

  // Ambardakiler
  const heldBox = el("ambar-held");
  clear(heldBox);
  let heldTotal = 0;
  shown.forEach((repo) => {
    const items = (repo.held || []).filter(matches);
    heldTotal += items.length;
    if (!items.length) return;
    if (grouped) heldBox.appendChild(repoHead(repo, false));
    items.forEach((item) => heldBox.appendChild(heldRow(repo, item)));
  });
  if (!heldTotal) {
    heldBox.appendChild(h("p", { class: "ambar-none", text: "Ambarda bekleyen PR yok." }));
  }

  // Onay bekleyen ambar PR'ları
  const pendingBox = el("ambar-pending");
  clear(pendingBox);
  let pendingTotal = 0;
  shown.forEach((repo) => {
    (repo.pending || []).forEach((item) => {
      pendingTotal += 1;
      pendingBox.appendChild(
        h("div", { class: "ambar-pending-line" }, [
          h("a", { href: item.url, target: "_blank", rel: "noopener", text: `#${item.number}` }),
          h("span", { class: "title", text: item.title }),
          grouped ? h("span", { class: "repo", text: repo.name }) : null,
        ])
      );
    });
  });
  el("ambar-pending-box").hidden = pendingTotal === 0;

  // Son birleşenler
  const listBox = el("ambar-list");
  clear(listBox);
  let listTotal = 0;
  shown.forEach((repo) => {
    const items = (repo.prs || []).filter(matches);
    if (grouped || repo.error || repo.warning) listBox.appendChild(repoHead(repo, true));
    if (repo.error) return;
    listTotal += items.length;
    if (!items.length) {
      listBox.appendChild(
        h("p", { class: "ambar-none", text: `Son ${ambarState.data.days} günde birleşen PR yok.` })
      );
      return;
    }
    items.forEach((item) => listBox.appendChild(prRow(repo, item)));
  });

  renderAmbarButtons();
}

function repoHead(repo, withStatus) {
  const head = h("div", { class: "ambar-repo-head" }, [
    h("a", { href: repo.url, target: "_blank", rel: "noopener", class: "repo-name", text: repo.name }),
    repo.base ? h("span", { class: "badge", text: repo.base }) : null,
  ]);
  if (withStatus && repo.error) {
    head.appendChild(
      h("span", { class: "ambar-error-note", text: repo.error.message, title: repo.error.detail || "" })
    );
  } else if (withStatus && repo.warning) {
    head.appendChild(h("span", { class: "warn-note", text: repo.warning }));
  }
  return head;
}

function prLink(url, label) {
  return url
    ? h("a", { class: "num", href: url, target: "_blank", rel: "noopener", text: label })
    : h("span", { class: "num", text: label });
}

function filesCell(files) {
  const list = files || [];
  if (!list.length) return h("span", { class: "files muted", text: "—" });
  const box = h("details", { class: "files" }, [
    h("summary", { text: `${list.length} dosya` }),
    h("ul", {}, list.slice(0, 200).map((name) => h("li", { text: name }))),
  ]);
  return box;
}

function prRow(repo, item) {
  const key = `${repo.id}:${item.number}`;
  const check = h("input", {
    type: "checkbox",
    "aria-label": `#${item.number} seç`,
    checked: !item.held && ambarState.pick.has(key),
    disabled: item.held,
    onchange: (event) => {
      if (event.target.checked) ambarState.pick.add(key);
      else ambarState.pick.delete(key);
      renderAmbarButtons();
    },
  });
  const note = h("input", {
    type: "text",
    class: "note",
    placeholder: "Not (isteğe bağlı)",
    maxlength: "500",
    value: ambarState.notes[key] || "",
    disabled: item.held,
    oninput: (event) => {
      ambarState.notes[key] = event.target.value;
    },
  });
  return h("div", { class: "ambar-row" + (item.held ? " is-held" : "") }, [
    h("label", { class: "pick" }, [check]),
    prLink(item.url, `#${item.number}`),
    h("div", { class: "ambar-main" }, [
      h("span", { class: "title", text: item.title }),
      item.held ? h("span", { class: "badge held-tag", text: "ambarda" }) : null,
    ]),
    h("span", { class: "who", text: item.author }),
    h("span", { class: "when", text: fmtWhen(item.merged_at) }),
    filesCell(item.files),
    note,
  ]);
}

function heldRow(repo, item) {
  const key = `${repo.id}:${item.sha}`;
  const check = h("input", {
    type: "checkbox",
    "aria-label": `${item.number ? "#" + item.number : item.short} ambardan çıkarılsın`,
    checked: !ambarState.unpicked.has(key),
    onchange: (event) => {
      if (event.target.checked) ambarState.unpicked.delete(key);
      else ambarState.unpicked.add(key);
      renderAmbarButtons();
    },
  });
  return h("div", { class: "ambar-row held" }, [
    h("label", { class: "pick" }, [check]),
    prLink(item.url, item.number ? `#${item.number}` : item.short),
    h("div", { class: "ambar-main" }, [h("span", { class: "title", text: item.title })]),
    h("span", { class: "who", text: item.author }),
    h("span", { class: "when", title: "Ambara alındığı an", text: fmtWhen(item.held_at) }),
    h("span", { class: "files muted", text: item.short }),
    h("span", {}),
  ]);
}

// --- seçim ve düğmeler -----------------------------------------------------------

function holdSelection() {
  const items = [];
  visibleRepos().forEach((repo) => {
    (repo.prs || []).forEach((item) => {
      const key = `${repo.id}:${item.number}`;
      if (!item.held && ambarState.pick.has(key)) {
        items.push({ repo_id: repo.id, repo: repo.name, number: item.number, title: item.title,
          note: (ambarState.notes[key] || "").trim() });
      }
    });
  });
  return items;
}

function releaseSelection() {
  const items = [];
  visibleRepos().forEach((repo) => {
    (repo.held || []).forEach((item) => {
      if (!ambarState.unpicked.has(`${repo.id}:${item.sha}`)) {
        items.push({ repo_id: repo.id, repo: repo.name, sha: item.sha,
          label: item.number ? `#${item.number}` : item.short, title: item.title });
      }
    });
  });
  return items;
}

function renderAmbarButtons() {
  const hold = holdSelection().length;
  const release = releaseSelection().length;
  const scope = ambarState.tab === AMBAR_ALL ? "" : " (bu depo)";
  const holdButton = el("ambar-hold");
  holdButton.textContent = hold ? `Ambara al (${hold})` : "Ambara al";
  holdButton.disabled = hold === 0 || ambarState.loading;
  const releaseButton = el("ambar-release");
  releaseButton.textContent = release
    ? `Ambardan çıkar (${release})${scope}`
    : "Ambardan çıkar";
  releaseButton.disabled = release === 0 || ambarState.loading;
}

function byRepo(items) {
  const groups = new Map();
  items.forEach((item) => {
    if (!groups.has(item.repo)) groups.set(item.repo, []);
    groups.get(item.repo).push(item);
  });
  return groups;
}

function confirmRun(op) {
  const hold = op === "al";
  const items = hold ? holdSelection() : releaseSelection();
  if (!items.length) return;
  const body = h("div", { class: "ambar-confirm" }, []);
  body.appendChild(
    h("p", {
      class: "hint",
      text: hold
        ? "Her depo için origin'in ana dalından ambar/al-... dalı açılır, seçilen PR'lar yeniden " +
          "eskiye geri alınır, dal itilir ve tek PR açılır. Ana dala yazılmaz, birleştirme yapılmaz."
        : "Her depo için ambar/cikar-... dalı açılır, ambara alırken yapılan geri almalar geri " +
          "alınır, dal itilir ve tek PR açılır. Ana dala yazılmaz, birleştirme yapılmaz.",
    })
  );
  byRepo(items).forEach((list, repo) => {
    body.appendChild(h("h4", { class: "ambar-confirm-repo", text: repo }));
    body.appendChild(
      h("ul", {}, list.map((item) =>
        h("li", {
          text: (hold ? `#${item.number} ` : `${item.label} `) + item.title +
            (item.note ? ` — Not: ${item.note}` : ""),
        })
      ))
    );
  });
  openModal(hold ? "Ambara al" : "Ambardan çıkar", body, [
    { label: "Vazgeç", onClick: closeModal },
    { label: hold ? "Ambara al" : "Ambardan çıkar", kind: "primary", onClick: () => runAmbar(op, items) },
  ]);
}

async function runAmbar(op, items) {
  const body = h("div", { class: "ambar-running" }, [
    h("p", { text: "Depolar işleniyor: fetch, dal, geri alma, push, PR. Bu biraz sürebilir..." }),
  ]);
  openModal(op === "al" ? "Ambara alınıyor" : "Ambardan çıkarılıyor", body, []);
  const payload = items.map((item) => op === "al"
    ? { repo_id: item.repo_id, number: item.number, note: item.note }
    : { repo_id: item.repo_id, sha: item.sha });
  try {
    const data = await api(`/api/ambar/${op}`, { method: "POST", body: JSON.stringify({ items: payload }) });
    showAmbarResult(op, data);
    if (op === "al") {
      data.results.filter((result) => result.ok).forEach((result) => {
        Array.from(ambarState.pick).forEach((key) => {
          if (key.startsWith(result.repo_id + ":")) ambarState.pick.delete(key);
        });
      });
    }
    if (typeof refreshCampaignBadge === "function") refreshCampaignBadge();
    // fetch ile: PR hemen birlestiyse (ya da baskasi birlestirdiyse) liste guncel olsun.
    loadAmbar(true);
  } catch (err) {
    closeModal();
    fail(err);
  }
}

function showAmbarResult(op, data) {
  const body = h("div", { class: "ambar-results" }, []);
  (data.results || []).forEach((result) => {
    const line = h("div", { class: "ambar-result " + (result.ok ? "ok" : "bad") }, [
      h("div", { class: "ambar-result-head" }, [
        h("strong", { text: result.name }),
        h("span", { class: "labels", text: (result.items || []).map((item) => item.label).join(", ") }),
      ]),
    ]);
    if (result.ok) {
      line.appendChild(
        h("div", {}, [
          document.createTextNode("PR açıldı: "),
          h("a", { href: result.pr_url, target: "_blank", rel: "noopener", text: `#${result.pr_number}` }),
          h("span", { class: "muted", text: `  ·  dal ${result.branch}` }),
        ])
      );
      if (result.warning) line.appendChild(h("div", { class: "warn-note", text: result.warning }));
    } else {
      const error = result.error || {};
      line.appendChild(h("div", { class: "ambar-result-title", text: error.title || "Hata" }));
      line.appendChild(h("div", { text: error.message || "" }));
      if (error.pr && error.pr.number) {
        line.appendChild(h("div", { class: "muted", text: `Çakışan PR: #${error.pr.number} ${error.pr.title || ""}` }));
      }
      if (error.files && error.files.length) {
        line.appendChild(h("div", { class: "muted", text: "Çakışan dosyalar:" }));
        line.appendChild(h("ul", { class: "ambar-files" }, error.files.map((name) => h("li", { text: name }))));
      }
      if (error.detail) {
        line.appendChild(h("details", {}, [h("summary", { text: "git çıktısı" }), h("pre", { text: error.detail })]));
      }
    }
    body.appendChild(line);
  });
  const reward = data.reward || {};
  if (reward.points || (reward.badges || []).length) {
    const parts = [];
    if (reward.points) parts.push(`+${reward.points} XP`);
    (reward.badges || []).forEach((badge) => parts.push(`Rozet: ${badge.label}`));
    body.appendChild(h("p", { class: "ambar-reward", text: parts.join(" · ") }));
  }
  const allOk = data.ok;
  openModal(allOk ? (op === "al" ? "Ambara alındı" : "Ambardan çıkarıldı") : "Sonuç", body, [
    { label: "Kapat", kind: "primary", onClick: closeModal },
  ], { wide: true });
}

// --- bağlama -------------------------------------------------------------------

document.addEventListener("DOMContentLoaded", () => {
  ambarState.tab = ambarRead(AMBAR_TAB_KEY, AMBAR_ALL) || AMBAR_ALL;
  const days = Number(ambarRead(AMBAR_DAYS_KEY, "30"));
  ambarState.days = Number.isFinite(days) && days > 0 ? days : 30;
  const select = el("ambar-days");
  if (![...select.options].some((option) => Number(option.value) === ambarState.days)) {
    ambarState.days = 30;
  }
  select.value = String(ambarState.days);
  select.addEventListener("change", () => {
    ambarState.days = Number(select.value) || 30;
    ambarWrite(AMBAR_DAYS_KEY, String(ambarState.days));
    loadAmbar(false);
  });
  el("ambar-search").addEventListener("input", (event) => {
    ambarState.query = event.target.value;
    renderAmbar();
  });
  el("ambar-reload").addEventListener("click", () => loadAmbar(true));
  el("ambar-hold").addEventListener("click", () => confirmRun("al"));
  el("ambar-release").addEventListener("click", () => confirmRun("cikar"));
  el("ambar-entry").addEventListener("click", showAmbar);
  el("ambar-entry").addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      showAmbar();
    }
  });
  refreshAmbarCount();
});
