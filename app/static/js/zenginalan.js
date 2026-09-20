// Zengin alan: duz metin yazan, Teams belirtecini CIP olarak gosteren kutu.
//
// Neden: `[[teams: Ad · Sohbet · 16 Eyl 2026 14:14|https://teams…]]` bir
// textarea'da uc satir kaplayan bir yazi yigini olarak duruyordu. Kullanici
// yapistirdigi anda cipi gormeli, kaydetmeyi beklememeli.
//
// Nasil: alan bir `contenteditable` kutudur. Icinde yalnizca DUZ METIN,
// satir sonlari ve ATOMIK cip elemanlari bulunur:
//
//   * cip `contenteditable="false"`: imlec icine girmez, surtuklenmez;
//   * tek Backspace/Delete butun cipi siler;
//   * cipe tiklamak kucuk bir menu acar: "Teams'te aç" / "Kaldır".
//
// Kayit bicimi DEGISMEZ: `deger()` metni yine belirtecli duz metin olarak
// verir, `degerYaz()` belirtecli metni cizer. Veritabaninda hep
// `[[teams:…|…]]` durur; Excel'e ve e-postaya cikarken `teamsLinkTemizle`
// onu siler.
//
// Alan bir textarea gibi davranir: `value` okunur/yazilir, `input` olayi
// gonderilir, `focus`/`select` calisir. Boylece `attachDuzelt` (duzelt.js) ve
// `app.js`'teki kaydetme akisi (Enter yeni satir, Ctrl+Enter kaydet, Esc
// vazgec, odak kaybinda kaydet) hic degismeden calisir.
//
// Bilesen bagimsizdir: `teamslink.js` disinda kimseye bagli degildir. Adlar
// `zenginAlan` on ekiyle baslar (bkz. tests/test_ui_assets.py).

// Satir acan elemanlar: tarayici Enter'i bir yerde `div` ile karsilarsa
// serilestirme yine dogru satiri verir.
const ZENGIN_BLOKLAR = ["div", "p", "li", "section", "article", "blockquote", "tr"];

// Cipin belirtecini tasiyan nitelik.
const ZENGIN_CIP_NITELIK = "data-teams";

// Acik duran cip menusu (ayni anda tek tane).
let zenginAlanAcikMenu = null;

// --- kucuk DOM yardimcilari ---------------------------------------------

/** Dugum bir cip mi? Oyleyse belirteci doner, degilse bos metin. */
function zenginAlanBelirtec(dugum) {
  if (!dugum || dugum.nodeType !== 1 || !dugum.getAttribute) return "";
  return dugum.getAttribute(ZENGIN_CIP_NITELIK) || "";
}

/** Dugum alanin icinde mi? */
function zenginAlanIcinde(alan, dugum) {
  let gezen = dugum;
  while (gezen) {
    if (gezen === alan) return true;
    gezen = gezen.parentNode;
  }
  return false;
}

/** Dugumden yukari cikarak cipi bulur (tiklama ikonun uzerine gelebilir). */
function zenginAlanCipBul(alan, dugum) {
  let gezen = dugum;
  while (gezen && gezen !== alan) {
    if (zenginAlanBelirtec(gezen)) return gezen;
    gezen = gezen.parentNode;
  }
  return null;
}

// --- serilestirme: DOM -> metin -----------------------------------------

/**
 * Alanin icerigini belirtecli duz metne cevirir.
 *
 * Kurallar: metin dugumu oldugu gibi, cip `data-teams`'teki belirtec olarak,
 * `br` satir sonu olarak gelir. Ust ustte duran son `br` SAYILMAZ: tarayici
 * son satiri gosterebilmek icin oraya bir dolgu koyar.
 */
function zenginAlanOku(kok) {
  const parcalar = [];
  zenginAlanGez(kok, parcalar);
  return parcalar.join("");
}

function zenginAlanGez(dugum, parcalar) {
  for (let kid = dugum.firstChild; kid; kid = kid.nextSibling) {
    if (kid.nodeType === 3) {
      parcalar.push(kid.nodeValue || "");
      continue;
    }
    if (kid.nodeType !== 1) continue;
    const belirtec = zenginAlanBelirtec(kid);
    if (belirtec) {
      parcalar.push(belirtec);
      continue;
    }
    const ad = String(kid.tagName || "").toLowerCase();
    if (ad === "br") {
      // Dolgu `br`: kendi ustundeki son cocuk. Metne satir sonu eklemez.
      if (!kid.nextSibling) continue;
      parcalar.push("\n");
      continue;
    }
    if (ZENGIN_BLOKLAR.indexOf(ad) >= 0) {
      if (parcalar.length) parcalar.push("\n");
      zenginAlanGez(kid, parcalar);
      continue;
    }
    zenginAlanGez(kid, parcalar);
  }
}

// --- cizim: metin -> DOM ------------------------------------------------

/** Belirtecli metnin cip/metin parcalari (`teamslink.js` ayristirir). */
function zenginAlanParcalari(metin) {
  if (typeof teamsLinkParcala !== "function") {
    const ham = String(metin === null || metin === undefined ? "" : metin);
    return ham ? [{ tur: "metin", metin: ham }] : [];
  }
  // Duzenleme kipinde duz adresler bag YAPILMAZ: yazarken tiklanan bir bag
  // imleci kacirir. Yalnizca Teams belirtecleri cipe doner.
  return teamsLinkParcala(metin).map((parca) =>
    parca.tur === "bag" ? { tur: "metin", metin: parca.metin } : parca
  );
}

/** Duzenleyicinin icine giren atomik cip. */
function zenginAlanCip(parca) {
  if (typeof teamsLinkCip === "function") return teamsLinkCip(parca, { atomik: true });
  const yedek = document.createElement("span");
  yedek.className = "teams-msg";
  yedek.setAttribute(ZENGIN_CIP_NITELIK, parca.metin || "");
  yedek.textContent = parca.etiket || "";
  return yedek;
}

/** Metni alana cizer: metin dugumleri, `br` satirlari ve cipler. */
function zenginAlanDoldur(alan, metin) {
  while (alan.firstChild) alan.removeChild(alan.firstChild);
  zenginAlanParcalari(metin).forEach((parca) => {
    if (parca.tur === "teams") {
      alan.appendChild(zenginAlanCip(parca));
      return;
    }
    // Satir sonlari metin dugumunde kalir: kutu `white-space: pre-wrap`.
    alan.appendChild(document.createTextNode(parca.metin));
  });
  zenginAlanBosTazele(alan);
  return alan;
}

/** Alan bos mu? Yer tutucu yazisi bu sinifa bakar. */
function zenginAlanBosTazele(alan) {
  if (!alan.classList) return;
  alan.classList.toggle("is-bos", zenginAlanOku(alan) === "");
}

// --- imlec ve yazma ------------------------------------------------------

/** Alanin icindeki imlec araligi; yoksa `null`. */
function zenginAlanAralik(alan) {
  const secim =
    typeof window !== "undefined" && window.getSelection ? window.getSelection() : null;
  if (!secim || !secim.rangeCount) return null;
  const aralik = secim.getRangeAt(0);
  if (!aralik || !zenginAlanIcinde(alan, aralik.startContainer)) return null;
  return aralik;
}

/** Imleci metnin sonuna koyar. */
function zenginAlanSonaGit(alan) {
  const secim =
    typeof window !== "undefined" && window.getSelection ? window.getSelection() : null;
  if (!secim || typeof document.createRange !== "function") return alan;
  try {
    const aralik = document.createRange();
    aralik.selectNodeContents(alan);
    aralik.collapse(false);
    secim.removeAllRanges();
    secim.addRange(aralik);
  } catch (err) {
    // Tarayici izin vermedi: odak yeter.
  }
  return alan;
}

/** Alanin tumunu secer (`attachDuzelt` uygularken kullaniyor). */
function zenginAlanTumunuSec(alan) {
  const secim =
    typeof window !== "undefined" && window.getSelection ? window.getSelection() : null;
  if (!secim || typeof document.createRange !== "function") return alan;
  try {
    const aralik = document.createRange();
    aralik.selectNodeContents(alan);
    secim.removeAllRanges();
    secim.addRange(aralik);
  } catch (err) {
    // Onemli degil.
  }
  return alan;
}

/** `input` olayi: `attachDuzelt` ve kaydetme akisi bunu dinliyor. */
function zenginAlanDegisti(alan) {
  zenginAlanBosTazele(alan);
  try {
    if (typeof Event === "function") {
      alan.dispatchEvent(new Event("input", { bubbles: true }));
    }
  } catch (err) {
    // Olay gonderilemedi; alanin icerigi yine de yerinde.
  }
}

/**
 * Imlecin oldugu yere belirtecli metin yazar.
 *
 * Once tarayicinin KENDI geri alma yigini denenir (`insertText` /
 * `insertHTML`); calismazsa parcalar elle araliga konur, imlec de olmazsa
 * metnin sonuna eklenir.
 */
function zenginAlanYaz(alan, metin) {
  const parcalar = zenginAlanParcalari(metin);
  if (!parcalar.length) return alan;
  const aralik = zenginAlanAralik(alan);
  parcalar.forEach((parca) => {
    if (parca.tur === "teams") {
      const cip = zenginAlanCip(parca);
      if (!zenginAlanHtmlYaz(cip) && !zenginAlanElleYaz(alan, cip, aralik)) {
        alan.appendChild(cip);
      }
      return;
    }
    if (zenginAlanDuzYaz(parca.metin)) return;
    const yazi = document.createTextNode(parca.metin);
    if (!zenginAlanElleYaz(alan, yazi, aralik)) alan.appendChild(yazi);
  });
  zenginAlanDegisti(alan);
  return alan;
}

/** Tarayicinin kendi yolu: duz metin. */
function zenginAlanDuzYaz(metin) {
  try {
    return document.execCommand("insertText", false, metin) === true;
  } catch (err) {
    return false;
  }
}

/** Tarayicinin kendi yolu: hazir dugum. Metin HTML olarak KURULMAZ. */
function zenginAlanHtmlYaz(dugum) {
  if (!dugum || typeof dugum.outerHTML !== "string") return false;
  try {
    return document.execCommand("insertHTML", false, dugum.outerHTML) === true;
  } catch (err) {
    return false;
  }
}

/** Yedek yol: dugumu imlecin yerine koyar; imlec yoksa `false` doner. */
function zenginAlanElleYaz(alan, dugum, aralik) {
  if (!aralik || typeof aralik.insertNode !== "function") return false;
  try {
    aralik.deleteContents();
    aralik.insertNode(dugum);
    aralik.setStartAfter(dugum);
    aralik.collapse(true);
    return true;
  } catch (err) {
    return false;
  }
}

// --- cip silme -----------------------------------------------------------

/**
 * Imlecin hemen yanindaki cip (varsa).
 *
 * `ileri` dogruysa Delete yonune, degilse Backspace yonune bakar. Secim
 * varsa (aralik kapali degilse) tarayicinin kendi silmesi dogru calisir.
 */
function zenginAlanKomsuCip(alan, ileri) {
  const aralik = zenginAlanAralik(alan);
  if (!aralik || aralik.collapsed !== true) return null;
  const kap = aralik.startContainer;
  const yer = aralik.startOffset;
  let komsu = null;
  if (kap.nodeType === 3) {
    const uzunluk = (kap.nodeValue || "").length;
    if (ileri ? yer < uzunluk : yer > 0) return null;
    komsu = ileri ? kap.nextSibling : kap.previousSibling;
  } else {
    const cocuklar = kap.childNodes || [];
    komsu = ileri ? cocuklar[yer] : cocuklar[yer - 1];
  }
  // Aradaki bos metin dugumleri atlanir.
  while (komsu && komsu.nodeType === 3 && !(komsu.nodeValue || "").length) {
    komsu = ileri ? komsu.nextSibling : komsu.previousSibling;
  }
  return komsu && zenginAlanBelirtec(komsu) ? komsu : null;
}

/** Cipi bir butun olarak siler; olabiliyorsa geri alma yiginiyla. */
function zenginAlanCipSil(alan, cip) {
  if (!cip) return false;
  zenginAlanMenuKapat();
  let oldu = false;
  const secim =
    typeof window !== "undefined" && window.getSelection ? window.getSelection() : null;
  if (secim && typeof document.createRange === "function") {
    try {
      const aralik = document.createRange();
      aralik.selectNode(cip);
      secim.removeAllRanges();
      secim.addRange(aralik);
      oldu = document.execCommand("delete") === true && !cip.parentNode;
    } catch (err) {
      oldu = false;
    }
  }
  if (!oldu && cip.parentNode) cip.parentNode.removeChild(cip);
  zenginAlanDegisti(alan);
  return true;
}

// --- cip menusu ----------------------------------------------------------

function zenginAlanMenuKapat() {
  const menu = zenginAlanAcikMenu;
  zenginAlanAcikMenu = null;
  if (menu && menu.parentNode) menu.parentNode.removeChild(menu);
}

/** Cipe tiklaninca acilan kucuk menu: "Teams'te aç" / "Kaldır". */
function zenginAlanMenuAc(alan, cip, parca) {
  zenginAlanMenuKapat();
  const menu = document.createElement("div");
  menu.className = "teams-menu";
  const dugme = (yazi, isle) => {
    const node = document.createElement("button");
    node.setAttribute("type", "button");
    node.textContent = yazi;
    // Odak alandan KACMASIN: cekmece yerel alani odak kaybinda kaydediyor.
    node.addEventListener("mousedown", (event) => event.preventDefault());
    node.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      isle();
    });
    menu.appendChild(node);
  };
  dugme("Teams'te aç", () => {
    zenginAlanMenuKapat();
    if (typeof teamsLinkAc === "function") teamsLinkAc(parca.url);
  });
  dugme("Kaldır", () => zenginAlanCipSil(alan, cip));
  menu.addEventListener("mousedown", (event) => event.preventDefault());

  if (cip.getBoundingClientRect) {
    const yer = cip.getBoundingClientRect();
    menu.style.left = Math.max(8, yer.left) + "px";
    menu.style.top = yer.bottom + 4 + "px";
  }
  document.body.appendChild(menu);
  zenginAlanAcikMenu = menu;
  return menu;
}

// --- kancalar ------------------------------------------------------------

function zenginAlanKancala(alan) {
  alan.addEventListener("paste", (event) => {
    const pano = event.clipboardData ? event.clipboardData.getData("text/plain") : "";
    // HTML yapistirma da metne iner: alanin icine yabanci imlem girmez.
    event.preventDefault();
    if (!pano) return;
    let cevrilmis = typeof teamsLinkYapistir === "function" ? teamsLinkYapistir(pano) : pano;
    // Cip metnin en sonunda kalirsa imlec arkasina gecemez: bir bosluk kalir.
    if (cevrilmis !== pano && /\]\]$/.test(cevrilmis)) cevrilmis += " ";
    zenginAlanYaz(alan, cevrilmis);
  });

  alan.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !(event.ctrlKey || event.metaKey || event.altKey)) {
      // Satir sonu DUZ METIN olarak girer: kutu `white-space: pre-wrap`.
      // Ctrl+Enter alanin sahibine birakilir (kaydeder).
      event.preventDefault();
      if (!zenginAlanDuzYaz("\n")) {
        const aralik = zenginAlanAralik(alan);
        const satir = document.createTextNode("\n");
        if (!zenginAlanElleYaz(alan, satir, aralik)) alan.appendChild(satir);
      }
      zenginAlanDegisti(alan);
      return;
    }
    if (event.key === "Backspace" || event.key === "Delete") {
      const cip = zenginAlanKomsuCip(alan, event.key === "Delete");
      if (cip) {
        event.preventDefault();
        zenginAlanCipSil(alan, cip);
      }
      return;
    }
    if (event.key === "Escape") zenginAlanMenuKapat();
  });

  alan.addEventListener("mousedown", (event) => {
    const cip = zenginAlanCipBul(alan, event.target);
    // Cipin icine imlec girmesin; odak da alandan cikmasin.
    if (cip) event.preventDefault();
  });

  alan.addEventListener("click", (event) => {
    const cip = zenginAlanCipBul(alan, event.target);
    if (!cip) {
      zenginAlanMenuKapat();
      return;
    }
    event.preventDefault();
    event.stopPropagation();
    const parcalar = zenginAlanParcalari(zenginAlanBelirtec(cip));
    const parca = parcalar.find((aday) => aday.tur === "teams") || { url: "" };
    zenginAlanMenuAc(alan, cip, parca);
  });

  alan.addEventListener("dragstart", (event) => {
    // Cip surtuklenmez: metnin ortasina yarim belirtec dusmesin.
    if (zenginAlanCipBul(alan, event.target)) event.preventDefault();
  });

  alan.addEventListener("input", () => zenginAlanBosTazele(alan));
  alan.addEventListener("blur", () => zenginAlanMenuKapat());
  return alan;
}

// --- bilesen -------------------------------------------------------------

/**
 * Cok satirli zengin alan kurar ve DUGUMU dondurur.
 *
 * Secenekler: `class` (ek siniflar), `placeholder`, `title`, `deger`.
 * Donen dugum bir textarea gibi davranir: `value`, `deger()`, `degerYaz()`,
 * `focus()`, `select()`, `imlecSona()`.
 */
function zenginAlanYap(secenekler) {
  const opt = secenekler || {};
  const alan = document.createElement("div");
  alan.className = "zengin-alan" + (opt.class ? " " + opt.class : "");
  alan.setAttribute("contenteditable", "true");
  alan.setAttribute("role", "textbox");
  alan.setAttribute("aria-multiline", "true");
  alan.setAttribute("tabindex", "0");
  if (opt.placeholder) {
    alan.setAttribute("data-placeholder", opt.placeholder);
    alan.setAttribute("aria-label", opt.placeholder);
  }
  if (opt.title) alan.setAttribute("title", opt.title);

  // Alani tanitan bayrak: `duzelt.js` ve `app.js` buna bakar.
  alan.zenginAlan = true;
  alan.deger = () => zenginAlanOku(alan);
  alan.degerYaz = (metin) => {
    zenginAlanDoldur(alan, metin);
    return alan;
  };
  alan.imlecSona = () => zenginAlanSonaGit(alan);
  alan.select = () => zenginAlanTumunuSec(alan);
  Object.defineProperty(alan, "value", {
    configurable: true,
    get: () => zenginAlanOku(alan),
    set: (metin) => zenginAlanDoldur(alan, metin),
  });

  zenginAlanKancala(alan);
  zenginAlanDoldur(alan, opt.deger || "");
  return alan;
}

// Menu sayfanin herhangi bir yerine basilinca kapanir.
if (typeof document !== "undefined" && document.addEventListener) {
  document.addEventListener("mousedown", (event) => {
    if (!zenginAlanAcikMenu) return;
    if (zenginAlanIcinde(zenginAlanAcikMenu, event.target)) return;
    zenginAlanMenuKapat();
  });
}
