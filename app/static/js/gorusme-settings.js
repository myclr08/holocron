// Ayarlar -> "Görüşme notları" kartı: algılama, aygıtlar, modeller, saklama.
//
// settings.js'ten ayrı durur: kendi uçları var (`/api/gorusme/ayar/...`) ve
// deneme kaydı ile Copilot sınaması burada yaşar. Kendi DOMContentLoaded
// dinleyicisini kurar.

const gorusmeSettings = { aygitlar: [], secili: {}, paketler: {} };

function gorusmeStatusBox() {
  return document.getElementById("gorusme-status");
}

function gorusmeField(id) {
  return document.getElementById(id);
}

/** Aygıt seçicileri: "Otomatik" her zaman ilk sırada durur. */
function fillGorusmeDevices(selectId, secili, girisMi) {
  const box = gorusmeField(selectId);
  while (box.firstChild) box.removeChild(box.firstChild);
  const otomatik = document.createElement("option");
  otomatik.value = "";
  otomatik.textContent = "Otomatik";
  box.appendChild(otomatik);
  gorusmeSettings.aygitlar
    .filter((aygit) => (girisMi ? aygit.giris : aygit.dongu || !aygit.giris))
    .forEach((aygit) => {
      const secenek = document.createElement("option");
      secenek.value = aygit.kimlik;
      secenek.textContent = aygit.ad;
      box.appendChild(secenek);
    });
  box.value = secili || "";
}

function renderGorusmePackages() {
  const paketler = gorusmeSettings.paketler || {};
  const eksik = [];
  if (!paketler.ses_yakalama) eksik.push("pyaudiowpatch (ses yakalama)");
  if (!paketler.faster_whisper) eksik.push("faster-whisper (yazıya dökme)");
  gorusmeField("gorusme-paketler").textContent = eksik.length
    ? "Eksik paketler: " + eksik.join(", ") + ". Kurulum README'de anlatılıyor."
    : "Ses yakalama ve yazıya dökme paketleri kurulu.";
}

async function loadGorusmeSettings(settings) {
  gorusmeField("gorusme-min-dakika").value = settings["calls.min_dakika"] || "4";
  gorusmeField("gorusme-whisper-model").value = settings["calls.whisper_model"] || "";
  gorusmeField("gorusme-whisper-klasor").value = settings["calls.whisper_klasor"] || "";
  gorusmeField("gorusme-copilot-proxy").value = settings["calls.copilot_proxy"] || "";
  gorusmeField("gorusme-klasor").value = settings["calls.calisma_klasoru"] || "";
  gorusmeField("gorusme-sablon").value = settings["calls.ozet_sablon"] || "";
  gorusmeField("gorusme-takip-acilista").checked = settings["calls.takip_acilista"] === "1";
  gorusmeField("gorusme-transkript-sakla").checked = settings["calls.transkripti_sakla"] === "1";
  gorusmeField("gorusme-ses-sakla").checked = settings["calls.sesi_sakla"] === "1";
  gorusmeField("gorusme-bildirim-baslangic").checked =
    settings["calls.bildirim_baslangic"] === "1";
  gorusmeField("gorusme-bildirim-hazir").checked = settings["calls.bildirim_hazir"] === "1";
  gorusmeField("gorusme-isleme-disinda").checked =
    settings["calls.isleme_gorusme_disinda"] === "1";
  gorusmeField("gorusme-unsupported").hidden = settings.gorusme_supported !== false;

  const sablon = await api("/api/gorusme/ayar/sablon");
  gorusmeField("gorusme-modeller").value = (sablon.modeller || []).join(", ");
  gorusmeField("gorusme-son-model").textContent = sablon.son_model
    ? "Son çalışan model: " + sablon.son_model
    : "Henüz özet üretilmedi.";
  gorusmeField("gorusme-sablon").placeholder = sablon.varsayilan.slice(0, 120) + "...";

  const aygitlar = await api("/api/gorusme/ayar/aygitlar");
  gorusmeSettings.aygitlar = aygitlar.aygitlar || [];
  gorusmeSettings.secili = aygitlar.secili || {};
  gorusmeSettings.paketler = aygitlar.paketler || {};
  fillGorusmeDevices("gorusme-mikrofon", settings["calls.mikrofon"], true);
  fillGorusmeDevices("gorusme-hoparlor", settings["calls.hoparlor"], false);
  gorusmeField("gorusme-klasor").placeholder = aygitlar.calisma_klasoru || "";
  renderGorusmePackages();
}

async function saveGorusmeSettings() {
  const modeller = gorusmeField("gorusme-modeller")
    .value.split(",")
    .map((ad) => ad.trim())
    .filter(Boolean);
  const payload = {
    "calls.min_dakika": gorusmeField("gorusme-min-dakika").value.trim() || "4",
    "calls.mikrofon": gorusmeField("gorusme-mikrofon").value,
    "calls.hoparlor": gorusmeField("gorusme-hoparlor").value,
    "calls.whisper_model": gorusmeField("gorusme-whisper-model").value.trim(),
    "calls.whisper_klasor": gorusmeField("gorusme-whisper-klasor").value.trim(),
    "calls.ozet_modelleri": JSON.stringify(modeller),
    "calls.copilot_proxy": gorusmeField("gorusme-copilot-proxy").value.trim(),
    "calls.ozet_sablon": gorusmeField("gorusme-sablon").value,
    "calls.calisma_klasoru": gorusmeField("gorusme-klasor").value.trim(),
    "calls.takip_acilista": gorusmeField("gorusme-takip-acilista").checked ? "1" : "0",
    "calls.transkripti_sakla": gorusmeField("gorusme-transkript-sakla").checked ? "1" : "0",
    "calls.sesi_sakla": gorusmeField("gorusme-ses-sakla").checked ? "1" : "0",
    "calls.bildirim_baslangic": gorusmeField("gorusme-bildirim-baslangic").checked ? "1" : "0",
    "calls.bildirim_hazir": gorusmeField("gorusme-bildirim-hazir").checked ? "1" : "0",
    "calls.isleme_gorusme_disinda": gorusmeField("gorusme-isleme-disinda").checked ? "1" : "0",
  };
  try {
    await api("/api/settings", { method: "PUT", body: JSON.stringify(payload) });
    setStatus(gorusmeStatusBox(), "Görüşme notu ayarları kaydedildi.", "ok");
  } catch (err) {
    setStatus(gorusmeStatusBox(), err.message, "error");
  }
}

/** Deneme kaydı: iki kanalı kaydeder, "ses var / yok" der. */
async function runGorusmeTrial() {
  const kutu = gorusmeField("gorusme-deneme-sonuc");
  kutu.textContent = "Kaydediliyor...";
  try {
    const sonuc = await api("/api/gorusme/ayar/deneme", {
      method: "POST",
      body: JSON.stringify({ saniye: 10 }),
    });
    while (kutu.firstChild) kutu.removeChild(kutu.firstChild);
    [
      ["mikrofon", sonuc.kanallar.mik],
      ["hoparlör", sonuc.kanallar.hop],
    ].forEach(([ad, kanal]) => {
      const satir = document.createElement("span");
      satir.className = kanal.ses_var ? "ok" : "no";
      satir.textContent = `${ad}: ${kanal.ses_var ? "ses var" : "ses yok"}`;
      satir.title = kanal.yol || "";
      kutu.appendChild(satir);
    });
    const yol = document.createElement("span");
    yol.className = "hint";
    yol.textContent = sonuc.klasor || "";
    kutu.appendChild(yol);
  } catch (err) {
    kutu.textContent = err.message;
  }
}

/** Copilot sınaması: seçili model ve vekil ile kısa bir istek. */
async function testGorusmeCopilot() {
  setStatus(gorusmeStatusBox(), "Copilot CLI sınanıyor...", "");
  try {
    const sonuc = await api("/api/gorusme/ayar/copilot-sina", { method: "POST" });
    setStatus(gorusmeStatusBox(), sonuc.mesaj, sonuc.calisiyor ? "ok" : "error");
    if (sonuc.calisiyor) {
      gorusmeField("gorusme-son-model").textContent = "Son çalışan model: " + sonuc.model;
    }
  } catch (err) {
    setStatus(gorusmeStatusBox(), err.message, "error");
  }
}

document.addEventListener("DOMContentLoaded", () => {
  document.getElementById("gorusme-save").addEventListener("click", saveGorusmeSettings);
  document.getElementById("gorusme-deneme").addEventListener("click", runGorusmeTrial);
  document.getElementById("gorusme-copilot-test").addEventListener("click", testGorusmeCopilot);
  api("/api/settings")
    // Uc `{settings: {...}}` doner: kart yalnizca ayar sozlugunu okur.
    .then((data) => loadGorusmeSettings(data.settings || {}))
    .catch((err) => setStatus(gorusmeStatusBox(), err.message, "error"));
});
