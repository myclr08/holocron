// Ayarlar -> "Güncelleme": sürüm denetimi, sürüm notları, adım adım kurulum
// ve yeniden başlatmadan sonra kendiliğinden yeniden bağlanma.
//
// Asıl iş sunucuda (`app/guncelle.py`) ve kapanan uygulamanın yerine dosyaları
// değiştiren yardımcı süreçte (`app/guncelleyici.py`) yapılır. Bu dosya
// yalnızca durumu çizer; uygulama kapanınca /api/health ile yeni sürümün
// ayağa kalkmasını bekler.

const updateUi = {
  info: null,
  polling: null,
  target: "",
  current: "",
};

// Yeni sürüm bağımlılıkları wheels/ içinden kurabilir; bekleme cömert.
const UPDATE_RECONNECT_MS = 6 * 60 * 1000;

function updateEl(id) {
  return document.getElementById(id);
}

function updateNode(tag, attrs, text) {
  const node = document.createElement(tag);
  Object.entries(attrs || {}).forEach(([name, value]) => node.setAttribute(name, value));
  if (text !== undefined) node.textContent = text;
  return node;
}

function updateStatus(message, kind) {
  setStatus(updateEl("update-status"), message, kind);
}

function updateStamp(iso) {
  const date = new Date(iso);
  if (!iso || Number.isNaN(date.getTime())) return "";
  return date.toLocaleString("tr-TR", { dateStyle: "short", timeStyle: "short" });
}

function renderUpdateSteps(status, failed) {
  const list = updateEl("update-steps");
  list.hidden = false;
  while (list.firstChild) list.removeChild(list.firstChild);
  (status.steps || []).forEach((step) => {
    let cls = "";
    if (step.done) cls = "done";
    else if (failed && step.id === status.step) cls = "failed";
    else if (step.active || (status.state === "restarting" && step.id === status.step)) cls = "active";
    let label = step.label;
    if (step.id === "download" && status.progress && status.progress.total && !step.done) {
      const pct = Math.min(100, Math.round((status.progress.done / status.progress.total) * 100));
      label += ` · %${pct}`;
    }
    list.appendChild(updateNode("li", cls ? { class: cls } : {}, label));
  });
}

function renderUpdateInfo(info) {
  updateUi.info = info;
  updateEl("update-checked").textContent = info.checked_at
    ? "Son denetim: " + updateStamp(info.checked_at)
    : "";
  const release = updateEl("update-release");
  const start = updateEl("update-start");
  const dot = updateEl("update-tab-dot");
  if (dot) dot.hidden = !info.newer;
  if (!info.newer) {
    release.hidden = true;
    start.hidden = true;
    updateStatus(`Holocron güncel: v${info.current} en son sürüm.`, "ok");
    return;
  }
  release.hidden = false;
  updateEl("update-release-title").textContent = `Yeni sürüm: v${info.latest}`;
  updateEl("update-notes").textContent = (info.notes || "Sürüm notu yok.").trim();
  if (!info.asset_ready || !info.checksums_ready) {
    start.hidden = true;
    updateStatus(
      `v${info.latest} yayımlanmış ama ${info.asset_ready ? "SHA-256 özet dosyası" : info.asset} henüz yok; ` +
        "doğrulanamayan paket kurulmaz. Biraz sonra yeniden deneyin ya da elle indirin.",
      "error"
    );
    return;
  }
  start.hidden = false;
  updateStatus(`v${info.latest} kurulabilir (${info.asset}).`, "ok");
}

async function checkForUpdates() {
  const button = updateEl("update-check");
  button.disabled = true;
  updateStatus("GitHub'a soruluyor...", "");
  try {
    const data = await api("/api/guncelleme/denetle", { method: "POST" });
    renderUpdateInfo(data.info);
  } catch (err) {
    updateStatus(err.message, "error");
  } finally {
    button.disabled = false;
  }
}

async function startUpdate() {
  const info = updateUi.info;
  if (!info || !info.newer) return;
  const ok = confirm(
    `Holocron v${info.latest} kurulsun mu?\n\n` +
      "1. Veritabanı ve anahtar Belgeler\\holocron\\holocron-yedek altına yedeklenir.\n" +
      "2. Paket indirilir ve SHA-256 özeti doğrulanır.\n" +
      "3. Holocron kapanır, yeni sürümle yeniden açılır (açılmazsa eskiye döner).\n\n" +
      "Verileriniz (görevler, ayarlar, Arşiv) olduğu gibi kalır."
  );
  if (!ok) return;
  updateEl("update-start").disabled = true;
  updateEl("update-check").disabled = true;
  try {
    const data = await api("/api/guncelleme/baslat", { method: "POST" });
    updateUi.target = info.latest;
    updateEl("update-start").hidden = true;
    renderUpdateSteps(data.status);
    updateStatus("Güncelleme başladı.", "");
    pollUpdate();
  } catch (err) {
    updateStatus(err.message, "error");
    updateEl("update-start").disabled = false;
    updateEl("update-check").disabled = false;
  }
}

function pollUpdate() {
  clearTimeout(updateUi.polling);
  updateUi.polling = setTimeout(async () => {
    let status;
    try {
      status = (await api("/api/guncelleme/durum")).status;
    } catch (err) {
      // Sunucu kapanmış: yeniden başlatma başlamıştır.
      waitForRestart();
      return;
    }
    if (status.target_version) updateUi.target = status.target_version;
    if (status.state === "error") {
      renderUpdateSteps(status, true);
      updateStatus(status.message || "Güncelleme durdu.", "error");
      updateEl("update-start").hidden = false;
      updateEl("update-start").disabled = false;
      updateEl("update-check").disabled = false;
      return;
    }
    renderUpdateSteps(status);
    if (status.state === "restarting") {
      updateStatus(status.message || "Holocron yeniden başlatılıyor...", "");
      waitForRestart();
      return;
    }
    pollUpdate();
  }, 700);
}

/** Uygulama kapanıp açılırken: nabız susar (şerit çıkmasın), /api/health beklenir. */
function waitForRestart() {
  if (typeof stopHeartbeat === "function") stopHeartbeat();
  const started = Date.now();
  let sawDown = false;
  const tick = async () => {
    let data = null;
    try {
      const response = await fetch("/api/health", { cache: "no-store" });
      if (response.ok) data = await response.json();
    } catch (err) {
      data = null;
    }
    if (!data) sawDown = true;
    if (data && data.version === updateUi.target) {
      updateStatus(`Holocron v${data.version} çalışıyor. Sayfa yenileniyor...`, "ok");
      setTimeout(() => window.location.reload(), 1200);
      return;
    }
    if (data && sawDown && data.version !== updateUi.target) {
      updateStatus(
        `Yeni sürüm açılmadı; Holocron v${data.version} sürümüne geri dönüldü. ` +
          "Ayrıntı veri klasöründeki guncelleme.log dosyasında.",
        "error"
      );
      setTimeout(() => window.location.reload(), 4000);
      return;
    }
    if (Date.now() - started > UPDATE_RECONNECT_MS) {
      updateStatus(
        "Holocron yeniden bağlanmadı. holocron.bat ile elle başlatın; ayrıntı guncelleme.log dosyasında.",
        "error"
      );
      return;
    }
    if (!data) updateStatus("Holocron yeniden başlatılıyor, bağlantı bekleniyor...", "");
    setTimeout(tick, 1500);
  };
  setTimeout(tick, 1500);
}

async function loadUpdatePane() {
  try {
    const data = await api("/api/guncelleme");
    const cached = data.cached || {};
    updateUi.current = cached.current;
    updateEl("update-current").textContent = "v" + (cached.current || "");
    updateEl("update-checked").textContent = cached.checked_at
      ? "Son denetim: " + updateStamp(cached.checked_at)
      : "Henüz denetlenmedi";
    const dot = updateEl("update-tab-dot");
    if (dot) dot.hidden = !cached.newer;
    const status = data.status || {};
    if (status.state === "running" || status.state === "restarting") {
      updateUi.target = status.target_version;
      updateEl("update-check").disabled = true;
      renderUpdateSteps(status);
      pollUpdate();
    } else if (cached.newer) {
      updateStatus(`v${cached.latest} yayımlandı. Ayrıntı için Güncellemeleri denetle.`, "");
    }
  } catch (err) {
    updateStatus(err.message, "error");
  }
}

document.addEventListener("DOMContentLoaded", () => {
  if (!updateEl("update-card")) return;
  updateEl("update-check").addEventListener("click", checkForUpdates);
  updateEl("update-start").addEventListener("click", startUpdate);
  loadUpdatePane();
});
