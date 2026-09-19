// "Düzelt": bir metin alanini Copilot'a duzelttiren kucuk bilesen.
//
// `attachDuzelt(alan)` bir textarea'yi sarmalar, sag alt kosesine hap biciminde
// bir dugme koyar ve butun durumlari (bekliyor / duzeltiliyor / oneri / hata)
// kendi yonetir. Fark KELIME duzeyinde burada hesaplanir: Copilot'tan yalnizca
// duz metin istenir, ayristirma derdi olmaz.
//
// Bilesen bagimsizdir: `app.js`'ten yalnizca varsa `toast` kullanilir, o da
// isteğe baglidir. Adlar `duzelt` on ekiyle baslar; ayni sayfadaki iki betik
// ayni ust duzey adi tanimlamasin diye (bkz. tests/test_ui_assets.py).

// Alan siniri sunucudakiyle ayni: daha uzun metinde dugme "çok uzun" der.
const DUZELT_SINIRI = 4000;

// Fark tablosu kelime sayisinin karesi kadar hucre tutar; cok uzun metinde
// tabloyu kurmak yerine tek blok fark gosterilir (tarayici kilitlenmesin).
const DUZELT_FARK_SINIRI = 1500000;

// Oneri panelindeki cipler. Ilk ikisi varsayilan acik.
const DUZELT_SECENEKLER = [
  { id: "imla", ad: "İmla ve noktalama", varsayilan: true },
  { id: "anlam", ad: "Anlam düşüklüğü", varsayilan: true },
  { id: "resmi", ad: "Daha resmi", varsayilan: false },
  { id: "kisa", ad: "Kısalt", varsayilan: false },
];

// Ayarlar ekrani kapatabilir; Copilot hic ayarlanmamissa dugme soluk kalir.
const duzeltAyar = { acik: true, hazir: false };

// Ayni anda tek duzeltme: ikinci istek baslatilamaz.
let duzeltCalisiyor = false;

/** `/api/settings` cevabindan dugmenin durumunu okur. */
function duzeltAyarla(settings) {
  const veri = settings || {};
  duzeltAyar.acik = veri["copilot.duzelt_acik"] !== "0";
  // Yol elle yazilmis, otomatik bulunmus ya da bir model calismissa hazir.
  duzeltAyar.hazir = !!(
    veri["copilot.yolu"] ||
    veri["copilot.yolu_son"] ||
    veri["copilot.son_model"]
  );
  return duzeltAyar;
}

// --- kucuk DOM yardimcilari ---------------------------------------------

function duzeltDugum(tag, attrs, children) {
  const node = document.createElement(tag);
  Object.entries(attrs || {}).forEach(([name, value]) => {
    if (value === null || value === undefined || value === false) return;
    if (name === "class") node.className = value;
    else if (name === "text") node.textContent = value;
    else if (name.startsWith("on")) node.addEventListener(name.slice(2), value);
    else node.setAttribute(name, value);
  });
  (children || []).forEach((child) => child && node.appendChild(child));
  return node;
}

function duzeltBosalt(node) {
  while (node && node.firstChild) node.removeChild(node.firstChild);
}

/** Sihirli degnek: emoji degil, kendi cizdigimiz SVG. */
function duzeltAsa() {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("class", "fix-icon");
  svg.setAttribute("viewBox", "0 0 16 16");
  svg.setAttribute("aria-hidden", "true");
  const yol = document.createElementNS("http://www.w3.org/2000/svg", "path");
  yol.setAttribute("d", "M10 2.1 10.7 3.7 12.3 4.4 10.7 5.1 10 6.7 9.3 5.1 7.7 4.4 9.3 3.7zM3 13l6-6M2.2 13.8l.8-.8");
  svg.appendChild(yol);
  return svg;
}

// --- kelime duzeyinde fark ----------------------------------------------

/** Metni kelimelere ve aralarindaki bosluklara ayirir (bosluk da parcadir). */
function duzeltBolumle(metin) {
  const ham = metin === null || metin === undefined ? "" : String(metin);
  if (!ham) return [];
  return ham.split(/(\s+)/).filter((parca) => parca !== "");
}

/**
 * Iki metnin kelime duzeyinde farki.
 *
 * En uzun ortak alt dizi (LCS) tablosu kurulur, sonra geri yurunur. Sonuc
 * `{tur: "ayni"|"del"|"ins", metin}` dizisidir; ardisik ayni turler birlesir.
 */
function duzeltFark(once, sonra) {
  const a = duzeltBolumle(once);
  const b = duzeltBolumle(sonra);
  const ops = [];
  const ekle = (tur, metin) => {
    const son = ops[ops.length - 1];
    if (son && son.tur === tur) son.metin += metin;
    else ops.push({ tur: tur, metin: metin });
  };
  const n = a.length;
  const m = b.length;
  if (!n && !m) return ops;
  if (!n || !m || (n + 1) * (m + 1) > DUZELT_FARK_SINIRI) {
    // Cok uzun metin: kelime kelime eslestirmek yerine tek blok fark.
    if (n) ekle("del", a.join(""));
    if (m) ekle("ins", b.join(""));
    return ops;
  }

  const genis = m + 1;
  const tablo = new Uint32Array((n + 1) * genis);
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      tablo[i * genis + j] =
        a[i] === b[j]
          ? tablo[(i + 1) * genis + j + 1] + 1
          : Math.max(tablo[(i + 1) * genis + j], tablo[i * genis + j + 1]);
    }
  }

  let i = 0;
  let j = 0;
  while (i < n && j < m) {
    if (a[i] === b[j]) {
      ekle("ayni", a[i]);
      i++;
      j++;
    } else if (tablo[(i + 1) * genis + j] >= tablo[i * genis + j + 1]) {
      ekle("del", a[i]);
      i++;
    } else {
      ekle("ins", b[j]);
      j++;
    }
  }
  while (i < n) ekle("del", a[i++]);
  while (j < m) ekle("ins", b[j++]);
  return ops;
}

/** Kac ayri yer degisti? Bitisik cikan+gelen tek degisiklik sayilir. */
function duzeltFarkSayisi(ops) {
  let sayi = 0;
  let acik = false;
  (ops || []).forEach((op) => {
    if (op.tur === "ayni") {
      acik = false;
      return;
    }
    if (!op.metin.trim()) return; // yalnizca bosluk oynamis
    if (!acik) {
      sayi++;
      acik = true;
    }
  });
  return sayi;
}

/** Fark parcalarini bir kutuya cizer: `hangi` "del" ya da "ins". */
function duzeltFarkKutusu(baslik, ops, hangi) {
  const kutu = duzeltDugum("div", {}, [duzeltDugum("h4", { text: baslik })]);
  (ops || []).forEach((op) => {
    if (op.tur !== "ayni" && op.tur !== hangi) return;
    if (op.tur === "ayni") kutu.appendChild(document.createTextNode(op.metin));
    else kutu.appendChild(duzeltDugum(hangi, { text: op.metin }));
  });
  return kutu;
}

// --- bilesen -------------------------------------------------------------

/**
 * Bir textarea'ya "Düzelt" dugmesini takar ve sarmalayici dugumu dondurur.
 *
 * Ayni alana ikinci kez cagrilmak zararsizdir (pencere yeniden cizilince
 * bilesen iki kez takilmasin): var olan sarmalayici doner.
 */
function attachDuzelt(alan, secenekler) {
  if (!alan) return null;
  if (alan.duzeltSarmal) return alan.duzeltSarmal;
  const opt = secenekler || {};

  const sarmal = duzeltDugum("div", { class: "fix-wrap" });
  const ana = alan.parentNode;
  if (ana) ana.insertBefore(sarmal, alan);
  sarmal.appendChild(alan);
  alan.duzeltSarmal = sarmal;

  const etiket = duzeltDugum("span", { class: "fix-label", text: "Düzelt" });
  const dugme = duzeltDugum("button", { class: "fix", type: "button" }, [
    duzeltAsa(),
    etiket,
  ]);
  sarmal.appendChild(dugme);

  let panel = null;
  let hata = null;
  let sonuc = null;
  let geriAl = null; // kendi tek adimlik geri almamiz
  let secili = DUZELT_SECENEKLER.filter((cip) => cip.varsayilan).map((cip) => cip.id);

  function hataTemizle() {
    if (hata && hata.parentNode) hata.parentNode.removeChild(hata);
    hata = null;
  }

  function hataGoster(metin, bagAdi) {
    hataTemizle();
    hata = duzeltDugum("p", { class: "fix-error" }, [
      duzeltDugum("span", { text: metin }),
      duzeltDugum("a", { href: "/settings#copilot", text: bagAdi || "Ayarlar'da sına" }),
    ]);
    sarmal.appendChild(hata);
  }

  function durumTazele() {
    if (!duzeltAyar.acik) {
      dugme.hidden = true;
      return;
    }
    dugme.hidden = false;
    if (duzeltCalisiyor && alan.duzeltAktif) return; // "Düzeltiliyor…" kalsin
    const uzunluk = (alan.value || "").length;
    const bos = !(alan.value || "").trim();
    const uzun = uzunluk > DUZELT_SINIRI;
    dugme.disabled = bos || uzun;
    dugme.classList.remove("busy");
    dugme.classList.toggle("on", !bos && !uzun && duzeltAyar.hazir);
    if (!duzeltAyar.hazir) dugme.title = "Copilot ayarlı değil";
    else if (uzun) dugme.title = "Metin çok uzun: " + uzunluk + " karakter, en fazla " + DUZELT_SINIRI;
    else if (bos) dugme.title = "Önce bir şeyler yazın";
    else dugme.title = "Metni düzelt (Ctrl+Shift+D)";
  }

  function mesgulAc() {
    duzeltCalisiyor = true;
    alan.duzeltAktif = true;
    alan.classList.add("is-duzeltiliyor");
    dugme.disabled = true;
    dugme.classList.remove("on");
    dugme.classList.add("busy");
    duzeltBosalt(dugme);
    dugme.appendChild(duzeltDugum("i", { class: "fix-spin", "aria-hidden": "true" }));
    dugme.appendChild(duzeltDugum("span", { class: "fix-label", text: "Düzeltiliyor…" }));
  }

  function mesgulKapat() {
    duzeltCalisiyor = false;
    alan.classList.remove("is-duzeltiliyor");
    duzeltBosalt(dugme);
    dugme.appendChild(duzeltAsa());
    dugme.appendChild(duzeltDugum("span", { class: "fix-label", text: "Düzelt" }));
  }

  function panelKapat() {
    if (panel && panel.parentNode) panel.parentNode.removeChild(panel);
    panel = null;
    sonuc = null;
    alan.hidden = false;
    alan.duzeltAktif = false;
    durumTazele();
  }

  function panelCiz() {
    const ops = duzeltFark(alan.value, sonuc.metin);
    const bilgi = [
      sonuc.model || "Copilot",
      (sonuc.sn === 0 || sonuc.sn ? sonuc.sn : "?") + " sn",
      duzeltFarkSayisi(ops) + " değişiklik",
    ].join(" · ");

    const cipler = duzeltDugum("div", { class: "chips" }, []);
    DUZELT_SECENEKLER.forEach((cip) => {
      const dugmecik = duzeltDugum("button", {
        class: "chip" + (secili.includes(cip.id) ? " on" : ""),
        type: "button",
        text: cip.ad,
        "aria-pressed": secili.includes(cip.id) ? "true" : "false",
      });
      dugmecik.addEventListener("click", () => {
        secili = secili.includes(cip.id)
          ? secili.filter((ad) => ad !== cip.id)
          : secili.concat([cip.id]);
        dugmecik.classList.toggle("on", secili.includes(cip.id));
        dugmecik.setAttribute("aria-pressed", secili.includes(cip.id) ? "true" : "false");
      });
      cipler.appendChild(dugmecik);
    });

    const yeni = duzeltDugum("div", { class: "suggest" }, [
      duzeltDugum("div", { class: "suggest-head" }, [
        duzeltDugum("b", { text: "Düzeltme önerisi" }),
        duzeltDugum("span", { class: "suggest-meta", text: bilgi }),
      ]),
      duzeltDugum("div", { class: "cmp" }, [
        duzeltFarkKutusu("Önce", ops, "del"),
        duzeltFarkKutusu("Sonra", ops, "ins"),
      ]),
      cipler,
      duzeltDugum("div", { class: "suggest-row" }, [
        duzeltDugum("button", { class: "small", type: "button", text: "Vazgeç", onclick: panelKapat }),
        duzeltDugum("button", {
          class: "small",
          type: "button",
          text: "Yeniden dene",
          onclick: () => {
            panelKapat();
            calistir();
          },
        }),
        duzeltDugum("button", {
          class: "small primary",
          type: "button",
          text: "Uygula",
          onclick: uygula,
        }),
      ]),
    ]);
    if (panel && panel.parentNode) panel.parentNode.removeChild(panel);
    panel = yeni;
    sarmal.appendChild(panel);
  }

  /**
   * Oneriyi alana yazar.
   *
   * Once tarayicinin KENDI geri alma yiginina dusmesi denenir
   * (`insertText`); calismazsa tek adimlik kendi geri almamiz devreye girer.
   */
  function uygula() {
    const yeniMetin = sonuc.metin;
    const eski = alan.value;
    panelKapat();
    alan.focus();
    let oldu = false;
    try {
      if (alan.setSelectionRange) alan.setSelectionRange(0, alan.value.length);
      else if (alan.select) alan.select();
      oldu = document.execCommand("insertText", false, yeniMetin);
    } catch (err) {
      oldu = false;
    }
    const kendiYigin = !oldu || alan.value !== yeniMetin;
    if (kendiYigin) alan.value = yeniMetin;
    try {
      if (typeof Event === "function") {
        alan.dispatchEvent(new Event("input", { bubbles: true }));
      }
    } catch (err) {
      // Olay gonderilemedi; alanin degeri yine de yerinde.
    }
    // `geriAl` olaydan SONRA yazilir: `input` dinleyicisi onu sifirliyor.
    geriAl = kendiYigin ? eski : null;
    durumTazele();
    if (typeof toast === "function") {
      toast("Düzeltme uygulandı", "Geri al: Ctrl+Z", "ok");
    }
  }

  async function calistir() {
    if (duzeltCalisiyor) return;
    hataTemizle();
    if (!duzeltAyar.acik) return;
    if (!duzeltAyar.hazir) {
      hataGoster("Copilot ayarlı değil.", "Ayarlar → Copilot");
      return;
    }
    const metin = alan.value || "";
    if (!metin.trim() || metin.length > DUZELT_SINIRI) return;
    mesgulAc();
    try {
      const cevap = await api("/api/copilot/duzelt", {
        method: "POST",
        body: JSON.stringify({ metin: metin, secenekler: secili }),
      });
      mesgulKapat();
      if (!cevap || cevap.hata || !cevap.metin) {
        alan.duzeltAktif = false;
        durumTazele();
        hataGoster((cevap && cevap.hata) || "Copilot düzeltilmiş metni vermedi.");
        return;
      }
      if (cevap.metin === metin) {
        alan.duzeltAktif = false;
        durumTazele();
        hataGoster("Düzeltilecek bir şey bulunamadı.", "Ayarlar → Copilot");
        return;
      }
      sonuc = cevap;
      alan.hidden = true;
      dugme.hidden = true;
      panelCiz();
    } catch (err) {
      mesgulKapat();
      alan.duzeltAktif = false;
      durumTazele();
      hataGoster(err.message || String(err));
    }
  }

  // Dugmeye basmak alandan odagi KACIRMASIN: yerel alan duzenleyicisi odak
  // kaybinda kaydediyor, aksi halde pencere dugme calismadan kapaniyordu.
  dugme.addEventListener("mousedown", (event) => event.preventDefault());
  dugme.addEventListener("click", (event) => {
    event.preventDefault();
    event.stopPropagation();
    calistir();
  });

  alan.addEventListener("input", () => {
    geriAl = null;
    durumTazele();
  });
  alan.addEventListener("keydown", (event) => {
    if (event.ctrlKey && event.shiftKey && (event.key === "D" || event.key === "d")) {
      event.preventDefault();
      calistir();
      return;
    }
    const geriTus = event.key === "z" || event.key === "Z";
    if (geriAl !== null && geriTus && (event.ctrlKey || event.metaKey) && !event.shiftKey) {
      event.preventDefault();
      alan.value = geriAl;
      geriAl = null;
      durumTazele();
    }
  });

  durumTazele();
  if (opt.ad) dugme.setAttribute("aria-label", opt.ad + ": metni düzelt");
  return sarmal;
}
