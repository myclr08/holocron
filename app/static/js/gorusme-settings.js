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
  // Yazıya dökme paketi buradan kurulabilir; ses yakalama paketi kurulumla
  // gelir, o eksikse paketi yenilemek gerekir.
  gorusmeField("gorusme-whisper-kur").hidden = paketler.faster_whisper !== false;
}

/** "Yazıya dökme paketini kur": pip alt süreçte, vekil ayarı onun ortamında. */
async function installWhisperPackage() {
  const dugme = gorusmeField("gorusme-whisper-kur");
  const kutu = gorusmeField("gorusme-whisper-kur-sonuc");
  dugme.disabled = true;
  kutu.textContent = "Kuruluyor... paket birkaç yüz megabayt, bu birkaç dakika sürebilir.";
  try {
    const sonuc = await api("/api/gorusme/ayar/whisper-kur", { method: "POST" });
    const kuyruk = sonuc.yeniden_kuyruga
      ? ` Bekleyen ${sonuc.yeniden_kuyruga} not yeniden kuyruğa alındı.`
      : "";
    kutu.textContent = (sonuc.mesaj || "") + kuyruk;
    setStatus(gorusmeStatusBox(), sonuc.mesaj || "", sonuc.kuruldu ? "ok" : "error");
    gorusmeSettings.paketler = Object.assign({}, gorusmeSettings.paketler, {
      faster_whisper: sonuc.whisper_var,
    });
    renderGorusmePackages();
  } catch (err) {
    kutu.textContent = err.message;
    setStatus(gorusmeStatusBox(), err.message, "error");
  } finally {
    dugme.disabled = false;
  }
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

/** Deneme kaydının tek satırı: "Mikrofon: <aygıt> — açıldı, 312 çerçeve, ses var". */
function trialLineText(ad, kanal) {
  const bilgi = kanal.aygit || {};
  const aygit = bilgi.ad || "aygıt seçilmedi";
  const parcalar = [kanal.acildi ? "açıldı" : "açılamadı", (kanal.cerceve || 0) + " çerçeve"];
  if (kanal.acildi && kanal.cerceve) parcalar.push(kanal.ses_var ? "ses var" : "ses yok");
  let metin = ad + ": " + aygit + " — " + parcalar.join(", ");
  if (kanal.hata) metin += " — " + kanal.hata;
  return metin;
}

/** Deneme kaydı: iki kanalı kaydeder, kanal başına ne olduğunu yazar.
 *
 * Uç artık hata durumunda da 200 döner (`sonuc.hata`): kullanıcı 500 yerine
 * neyin olmadığını okur. */
async function runGorusmeTrial() {
  const kutu = gorusmeField("gorusme-deneme-sonuc");
  kutu.textContent = "Kaydediliyor... 10 saniye.";
  const yaz = (metin, sinif) => {
    const satir = document.createElement("span");
    satir.className = sinif;
    satir.textContent = metin;
    kutu.appendChild(satir);
  };
  try {
    const sonuc = await api("/api/gorusme/ayar/deneme", {
      method: "POST",
      body: JSON.stringify({ saniye: 10 }),
    });
    while (kutu.firstChild) kutu.removeChild(kutu.firstChild);
    if (sonuc.hata) yaz(sonuc.hata, "no");
    const kanallar = sonuc.kanallar || {};
    [
      ["Mikrofon", kanallar.mik],
      ["Duyduğum ses", kanallar.hop],
    ].forEach(([ad, kanal]) => {
      if (!kanal) return;
      const iyi = kanal.acildi && kanal.cerceve > 0 && kanal.ses_var && !kanal.hata;
      yaz(trialLineText(ad, kanal), iyi ? "ok" : "no");
    });
    (sonuc.oneriler || []).forEach((oneri) => yaz(oneri, "hint"));
    if (sonuc.klasor) yaz(sonuc.klasor, "hint");
  } catch (err) {
    while (kutu.firstChild) kutu.removeChild(kutu.firstChild);
    yaz(err.message, "no");
  }
}

/** "Modeli sına": yazıya dökme modeli yüklenebiliyor mu? Kuyruğu bloklamaz. */
async function testWhisperModel() {
  const dugme = gorusmeField("gorusme-model-sina");
  const kutu = gorusmeField("gorusme-model-sina-sonuc");
  dugme.disabled = true;
  kutu.textContent = "Model yükleniyor...";
  try {
    // Alandaki değerler kaydedilmemiş olabilir: uca onları yollarız.
    const sonuc = await api("/api/gorusme/ayar/model-sina", {
      method: "POST",
      body: JSON.stringify({
        model: gorusmeField("gorusme-whisper-model").value.trim(),
        klasor: gorusmeField("gorusme-whisper-klasor").value.trim(),
      }),
    });
    kutu.textContent = sonuc.mesaj || "";
    setStatus(gorusmeStatusBox(), sonuc.mesaj || "", sonuc.calisiyor ? "ok" : "error");
  } catch (err) {
    kutu.textContent = err.message;
    setStatus(gorusmeStatusBox(), err.message, "error");
  } finally {
    dugme.disabled = false;
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
  document.getElementById("gorusme-whisper-kur").addEventListener("click", installWhisperPackage);
  document.getElementById("gorusme-model-sina").addEventListener("click", testWhisperModel);
  api("/api/settings")
    // Uc `{settings: {...}}` doner: kart yalnizca ayar sozlugunu okur.
    .then((data) => loadGorusmeSettings(data.settings || {}))
    .catch((err) => setStatus(gorusmeStatusBox(), err.message, "error"));
});
