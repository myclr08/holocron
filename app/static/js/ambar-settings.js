// Ayarlar -> "Ambar": GitHub API adresi, tokenlar, git yolu, depo listesi.
//
// settings.js'ten ayrı durur: kendi ucu (`/api/ambar/settings`) var. Tokenlar
// sunucudan hiç gelmez; yalnızca "ayarlı / ayarsız" bilgisi görünür.

const ambarSettings = { repos: [] };

function ambarSettingsStatus() {
  return document.getElementById("ambar-status");
}

function ambarNode(tag, attrs, children) {
  const node = document.createElement(tag);
  Object.entries(attrs || {}).forEach(([name, value]) => {
    if (value === null || value === undefined || value === false) return;
    if (name === "class") node.className = value;
    else if (name === "text") node.textContent = value;
    else if (name.startsWith("on")) node.addEventListener(name.slice(2), value);
    else node.setAttribute(name, value === true ? "" : value);
  });
  (children || []).forEach((child) => child && node.appendChild(child));
  return node;
}

function ambarRepoLine(repo) {
  const line = ambarNode("div", { class: "ambar-repo-line" }, []);
  line.dataset.id = repo.id || "";
  const name = ambarNode("input", {
    type: "text", class: "ambar-repo-name", placeholder: "kurulus/depo",
    value: repo.name || "", "aria-label": "GitHub depo adı",
  });
  const path = ambarNode("input", {
    type: "text", class: "ambar-repo-path", placeholder: "C:\\kod\\depo",
    value: repo.path || "", "aria-label": "Yerel klon klasörü",
  });
  const token = ambarNode("input", {
    type: "password", class: "ambar-repo-token", autocomplete: "off",
    placeholder: repo.token_set ? "ayrı token ayarlı (değiştirmek için yazın)" : "boş = genel token",
    "aria-label": "Depo token'ı",
  });
  const check = ambarNode("button", {
    type: "button", text: "Sına", title: "git, klon, origin ve GitHub erişimini dener",
    disabled: !repo.id,
    onclick: () => testAmbarRepo(repo.id, line),
  });
  const remove = ambarNode("button", {
    type: "button", class: "danger", text: "Sil",
    onclick: () => {
      const result = line.nextSibling;
      if (result && result.classList && result.classList.contains("ambar-check")) result.remove();
      line.remove();
    },
  });
  line.appendChild(name);
  line.appendChild(path);
  line.appendChild(token);
  line.appendChild(check);
  line.appendChild(remove);
  return line;
}

function renderAmbarSettings(data) {
  document.getElementById("ambar-api-url").value = data.api_url || data.default_api_url || "";
  document.getElementById("ambar-git").value = data.git_path || "";
  document.getElementById("ambar-proxy").value = data.proxy || "";
  document.getElementById("ambar-token").value = "";
  document.getElementById("ambar-token-state").textContent = data.token_set
    ? "Token ayarlı. Değiştirmek için yenisini yazın."
    : "Token ayarlı değil.";
  const box = document.getElementById("ambar-repos");
  while (box.firstChild) box.removeChild(box.firstChild);
  ambarSettings.repos = data.repos || [];
  box.appendChild(
    ambarNode("div", { class: "ambar-repo-line ambar-repo-labels", "aria-hidden": "true" }, [
      ambarNode("span", { text: "GitHub deposu" }),
      ambarNode("span", { text: "Yerel klon klasörü" }),
      ambarNode("span", { text: "Ayrı token (isteğe bağlı)" }),
    ])
  );
  ambarSettings.repos.forEach((repo) => box.appendChild(ambarRepoLine(repo)));
  if (!ambarSettings.repos.length) box.appendChild(ambarRepoLine({}));
}

async function loadAmbarSettings() {
  const data = await api("/api/ambar/settings");
  renderAmbarSettings(data.ambar || {});
}

async function saveAmbarSettings() {
  const repos = [];
  document.querySelectorAll("#ambar-repos .ambar-repo-line").forEach((line) => {
    const name = line.querySelector(".ambar-repo-name").value.trim();
    const path = line.querySelector(".ambar-repo-path").value.trim();
    const token = line.querySelector(".ambar-repo-token").value.trim();
    if (!name && !path) return;
    const item = { id: line.dataset.id || "", name: name, path: path };
    if (token) item.token = token;
    repos.push(item);
  });
  const payload = {
    api_url: document.getElementById("ambar-api-url").value.trim(),
    git_path: document.getElementById("ambar-git").value.trim(),
    proxy: document.getElementById("ambar-proxy").value.trim(),
    repos: repos,
  };
  const token = document.getElementById("ambar-token").value.trim();
  if (token) payload.token = token;
  try {
    const data = await api("/api/ambar/settings", { method: "PUT", body: JSON.stringify(payload) });
    renderAmbarSettings(data.ambar || {});
    setStatus(ambarSettingsStatus(), "Ambar ayarları kaydedildi.", "ok");
  } catch (err) {
    setStatus(ambarSettingsStatus(), err.message, "error");
  }
}

async function testAmbarRepo(repoId, line) {
  if (!repoId) return;
  let box = line.nextSibling;
  if (!box || !box.classList || !box.classList.contains("ambar-check")) {
    box = ambarNode("div", { class: "ambar-check" }, []);
    line.parentNode.insertBefore(box, line.nextSibling);
  }
  box.textContent = "Sınanıyor...";
  try {
    const data = await api(`/api/ambar/test/${encodeURIComponent(repoId)}`, { method: "POST" });
    while (box.firstChild) box.removeChild(box.firstChild);
    (data.steps || []).forEach((step) => {
      box.appendChild(
        ambarNode("div", {}, [
          ambarNode("span", { class: step.ok ? "ok" : "bad", text: step.ok ? "✓ " : "✗ " }),
          document.createTextNode(`${step.label}: ${step.message}`),
        ])
      );
    });
  } catch (err) {
    box.textContent = err.message;
  }
}

document.addEventListener("DOMContentLoaded", () => {
  document.getElementById("ambar-save").addEventListener("click", saveAmbarSettings);
  document.getElementById("ambar-add-repo").addEventListener("click", () => {
    document.getElementById("ambar-repos").appendChild(ambarRepoLine({}));
  });
  loadAmbarSettings().catch((err) => setStatus(ambarSettingsStatus(), err.message, "error"));
});
