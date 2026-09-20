// Teams mesaj baglantisi: panodaki blok belirtece, gosterimde cipe donusur.
//
// Teams'te bir mesaja sag tik -> "Bağlantıyı kopyala" panoya IKI parca koyar:
// bir on satir (gonderen, sohbet adi, gonderme zamani) ve bir derin baglanti.
// Ikisi de oldugu gibi bir aciklama alanina yapistirilinca metin bogulur.
// Bunun yerine blok tek satirlik bir belirtece cevrilir:
//
//     [[teams: Deniz Akgün · Ödeme ekibi · 16 Eyl 2026 14:14|https://teams…]]
//
// Belirtec DUZ METINDIR: veritabani semasi degismez, kullanici duzenleme
// kipinde onu gorur ve silebilir, kaydetme akisi hic degismez. Salt-okunur
// gosterimlerde `teamsLinkCiz` onu tiklanabilir bir cipe cevirir.
//
// Belirtec UYGULAMANIN ICINDE KALIR: Excel dokumune ve e-posta / Teams mesaj
// govdelerine hic gitmez, oralarda `teamsLinkTemizle` ile silinir. Python esi
// `app/teamslink.py` icindeki `temizle`; iki tarafin davranisi ayni.
//
// Bilesen bagimsizdir, `app.js`'e bagli degildir. Adlar `teamsLink` on ekiyle
// baslar; ayni sayfadaki iki betik ayni ust duzey adi tanimlamasin diye
// (bkz. tests/test_ui_assets.py).

// Belirtecin kendisi. Etiket ve adres `|` ile ayrilir; ikisi de `[`, `]` ve
// `|` icermez, bu yuzden ic ice gecme sorunu yoktur.
const TEAMSLINK_BELIRTEC = /\[\[teams:([^[\]|]*)\|([^[\]|]*)\]\]/g;

// Teams mesaj derin baglantisi: tarayici bicimi ya da `msteams:` esdegeri.
const TEAMSLINK_ADRES =
  /(?:https?:\/\/(?:[a-z0-9-]+\.)*teams\.microsoft\.com|msteams:(?:\/\/)?)\/l\/message\/[^\s<>"']+/gi;

// Metin icindeki duz baglantilar da tiklanabilir olsun.
const TEAMSLINK_DUZ_ADRES = /https?:\/\/[^\s<>"']+/gi;

// Adresin sonuna yapisan noktalama baglantiya dahil edilmez.
const TEAMSLINK_SON_NOKTALAMA = /[.,;:!?)\]}'"»…]+$/;

// Etiketin parcalari bununla ayrilir: "Ad · Sohbet · 16 Eyl 2026 14:14".
const TEAMSLINK_AYRAC = " · ";

// Cipte gorunen etiketin ust siniri. Fazlasi "…" ile kirpilir; tam bilgi
// `title`'da durur. Sinir hucre yuksekligine gore secildi: tek satir.
const TEAMSLINK_ETIKET_SINIRI = 48;

// Sohbet adinin sonundaki sayac: Teams ayni basliktan ikincisini "… 2" diye
// adlandiriyor. Karsilastirmada bu ek dusurulur.
const TEAMSLINK_SAYAC = /\s*[(\[]?\d{1,3}[)\]]?$/;

// On satiri olmayan baglantinin adi.
const TEAMSLINK_ADSIZ = "Teams mesajı";

// Ay kisaltmalari: Turkce ve Ingilizce, ilk uc harfe indirgenmis.
const TEAMSLINK_AYLAR = {
  oca: 1, sub: 2, şub: 2, mar: 3, nis: 4, may: 5, haz: 6, tem: 7,
  ağu: 8, agu: 8, eyl: 9, eki: 10, kas: 11, ara: 12,
  jan: 1, feb: 2, apr: 4, jun: 6, jul: 7, aug: 8, sep: 9, oct: 10, nov: 11, dec: 12,
};

// Belirtecte ve cipte gorunen ay adlari (Turkce kisa).
const TEAMSLINK_AY_ADLARI = [
  "Oca", "Şub", "Mar", "Nis", "May", "Haz", "Tem", "Ağu", "Eyl", "Eki", "Kas", "Ara",
];

// On satirin Teams'ten geldigini ele veren kelimeler (Turkce/Ingilizce arayuz).
const TEAMSLINK_ON_SATIR = /sohbetinde|gönderildi|gönderme zamanı|kanalında|sent\b|chat\b/i;

// Uygulama protokolune taninan sure; sonrasinda tarayici yedegi acilir.
const TEAMSLINK_YEDEK_MS = 700;

// Mesaj kimligi Unix milisaniye zaman damgasidir; makul bir araliktaysa
// zamani ondan turetiriz (2001 -> 2100).
const TEAMSLINK_EN_ERKEN_MS = 978307200000;
const TEAMSLINK_EN_GEC_MS = 4102444800000;

// --- zaman ---------------------------------------------------------------

/** Iki haneli sayi. */
function teamsLinkIki(sayi) {
  return String(sayi).padStart(2, "0");
}

/** Tarihi belirtecin ve cipin kullandigi bicime yazar: "16 Eyl 2026 14:14". */
function teamsLinkZamanYaz(an) {
  if (!an || isNaN(an.getTime())) return "";
  return (
    `${an.getDate()} ${TEAMSLINK_AY_ADLARI[an.getMonth()]} ${an.getFullYear()} ` +
    `${teamsLinkIki(an.getHours())}:${teamsLinkIki(an.getMinutes())}`
  );
}

/** Ay adini (Tur/Ing, kisa ya da uzun) 1-12 arasina cevirir; bulunamazsa 0. */
function teamsLinkAy(kelime) {
  const ad = String(kelime || "").toLowerCase().slice(0, 3);
  return TEAMSLINK_AYLAR[ad] || 0;
}

/**
 * On satirdaki gonderme zamanini cozer.
 *
 * Teams arayuzun diline gore "Eyl 16, 2026, 14:14" ya da "16 Eyl 2026 14:14"
 * yazar; Ingilizce arayuzde saat 12'lik olabilir ("2:14 PM"). Hicbiri
 * tutmazsa `null` doner ve zaman mesaj kimliginden turetilir.
 */
function teamsLinkZamanCoz(metin) {
  const ham = String(metin || "");
  const saatParca =
    ham.match(/(\d{1,2}):(\d{2})(?::\d{2})?\s*([APap])\.?\s*[Mm]\.?/) ||
    ham.match(/(\d{1,2}):(\d{2})/);
  if (!saatParca) return null;
  let saat = Number(saatParca[1]);
  const dakika = Number(saatParca[2]);
  const oglen = (saatParca[3] || "").toLowerCase();
  if (oglen === "p" && saat < 12) saat += 12;
  if (oglen === "a" && saat === 12) saat = 0;

  // "Eyl 16, 2026" / "Sep 16, 2026"
  let gun = 0;
  let ay = 0;
  let yil = 0;
  const harfli = ham.match(/([A-Za-zÇĞİÖŞÜçğıöşü]{3,9})\.?\s+(\d{1,2}),?\s+(\d{4})/);
  const oncegun = ham.match(/(\d{1,2})\s+([A-Za-zÇĞİÖŞÜçğıöşü]{3,9})\.?,?\s+(\d{4})/);
  const sayili = ham.match(/(\d{1,2})[./](\d{1,2})[./](\d{4})/);
  if (harfli && teamsLinkAy(harfli[1])) {
    ay = teamsLinkAy(harfli[1]);
    gun = Number(harfli[2]);
    yil = Number(harfli[3]);
  } else if (oncegun && teamsLinkAy(oncegun[2])) {
    gun = Number(oncegun[1]);
    ay = teamsLinkAy(oncegun[2]);
    yil = Number(oncegun[3]);
  } else if (sayili) {
    gun = Number(sayili[1]);
    ay = Number(sayili[2]);
    yil = Number(sayili[3]);
  } else {
    return null;
  }
  if (!gun || !ay || ay > 12 || gun > 31) return null;
  const an = new Date(yil, ay - 1, gun, saat, dakika, 0, 0);
  return isNaN(an.getTime()) ? null : an;
}

/** Mesaj kimligi (ms epoch) -> yerel zaman. Kimlik makul degilse `null`. */
function teamsLinkKimlikZamani(kimlik) {
  const sayi = Number(String(kimlik || "").trim());
  if (!sayi || !isFinite(sayi)) return null;
  if (sayi < TEAMSLINK_EN_ERKEN_MS || sayi > TEAMSLINK_EN_GEC_MS) return null;
  const an = new Date(sayi);
  return isNaN(an.getTime()) ? null : an;
}

// --- adres ---------------------------------------------------------------

/** Adresin sorgu parametreleri; `URL` kullanilmaz, `msteams:` onu sasirtiyor. */
function teamsLinkParametreler(url) {
  const yer = String(url || "").indexOf("?");
  const sonuc = {};
  if (yer < 0) return sonuc;
  String(url)
    .slice(yer + 1)
    .split("&")
    .forEach((parca) => {
      if (!parca) return;
      const esit = parca.indexOf("=");
      const ad = esit < 0 ? parca : parca.slice(0, esit);
      const deger = esit < 0 ? "" : parca.slice(esit + 1);
      try {
        sonuc[decodeURIComponent(ad)] = decodeURIComponent(deger.replace(/\+/g, " "));
      } catch (err) {
        sonuc[ad] = deger;
      }
    });
  return sonuc;
}

/** `/l/message/<sohbet>/<mesaj>` yolundaki iki kimlik. */
function teamsLinkYol(url) {
  const bulunan = String(url || "").match(/\/l\/message\/([^/?#]+)(?:\/([^/?#]+))?/);
  if (!bulunan) return { sohbet: "", mesaj: "" };
  const coz = (deger) => {
    try {
      return decodeURIComponent(deger || "");
    } catch (err) {
      return deger || "";
    }
  };
  return { sohbet: coz(bulunan[1]), mesaj: coz(bulunan[2]) };
}

/**
 * Adresin `msteams:` karsiligi.
 *
 * `app/teams.py` ile ayni kalip: once uygulama protokolu denenir, acilmazsa
 * `https` surumune dusulur. Iki adresin parametreleri birebir aynidir.
 */
function teamsLinkUygulamaAdresi(url) {
  const ham = String(url || "").trim();
  if (!ham) return "";
  if (/^msteams:/i.test(ham)) return ham;
  const yer = ham.indexOf("/l/message/");
  if (yer < 0) return "";
  return "msteams:" + ham.slice(yer);
}

/** `msteams:` adresinin tarayici karsiligi. */
function teamsLinkWebAdresi(url) {
  const ham = String(url || "").trim();
  if (!ham) return "";
  if (/^https?:/i.test(ham)) return ham;
  const yer = ham.indexOf("/l/message/");
  if (yer < 0) return "";
  return "https://teams.microsoft.com" + ham.slice(yer);
}

// --- panodaki blogu belirtece cevirme ------------------------------------

/** Etikette `|`, `[`, `]` duramaz; bosluklar tek boslugga iner. */
function teamsLinkEtiketTemiz(metin) {
  return String(metin || "")
    .replace(/[[\]|]/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

/** Gonderenin TAM basligi: on satirda `|` oncesi kalan. */
function teamsLinkBaslikCoz(onSatir) {
  const ham = String(onSatir || "");
  const boru = ham.indexOf("|");
  return teamsLinkEtiketTemiz(boru < 0 ? ham : ham.slice(0, boru));
}

/**
 * Baslikta gonderenin adi: ilk `-` oncesi.
 *
 * Teams "Ad Soyad-Şirket-Birim-Unvan" yazar; cipte yalnizca ad durur, geri
 * kalani `title`'a birakilir.
 */
function teamsLinkAdKirp(baslik) {
  const ham = teamsLinkEtiketTemiz(baslik);
  const tire = ham.indexOf("-");
  return teamsLinkEtiketTemiz(tire < 0 ? ham : ham.slice(0, tire));
}

/**
 * On satirdan gonderen adini cikarir.
 *
 * Tire hic yoksa `|` oncesindeki butun metin ad sayilir.
 */
function teamsLinkAdCoz(onSatir) {
  return teamsLinkAdKirp(teamsLinkBaslikCoz(onSatir));
}

/** Sondaki sayaci atar: "Deniz Akgün-… 2" -> "Deniz Akgün-…". */
function teamsLinkSayacKirp(metin) {
  return String(metin || "").replace(TEAMSLINK_SAYAC, "").trim();
}

/**
 * Sohbet adi gondereni tekrar mi ediyor?
 *
 * Birebir yazismalarda Teams sohbete gonderenin basligini veriyor ("Ad
 * Soyad-Şirket-Birim-Unvan", ikincisine " 2" ekleyerek). Boyle bir ad cipte
 * hicbir sey anlatmaz: atlanir.
 */
function teamsLinkSohbetTekrar(baslik, ad, sohbet) {
  const konu = teamsLinkEtiketTemiz(sohbet);
  if (!konu) return true;
  const tam = teamsLinkEtiketTemiz(baslik);
  const kisa = teamsLinkEtiketTemiz(ad);
  if (kisa && konu === kisa) return true;
  if (!tam) return false;
  if (konu === tam || konu.indexOf(tam) === 0) return true;
  return teamsLinkSayacKirp(konu) === teamsLinkSayacKirp(tam);
}

/** "Ad · Sohbet · zaman"; gondereni tekrar eden sohbet adi yazilmaz. */
function teamsLinkEtiketKur(baslik, sohbet, zaman) {
  const ad = teamsLinkAdKirp(baslik) || TEAMSLINK_ADSIZ;
  const parcalar = [ad];
  if (sohbet && !teamsLinkSohbetTekrar(baslik, ad, sohbet)) parcalar.push(sohbet);
  if (zaman) parcalar.push(zaman);
  return parcalar.join(TEAMSLINK_AYRAC);
}

/**
 * Hazir bir etiketi parcalarina ayirir: `{baslik, sohbet, zaman}`.
 *
 * Son parca cozulebilen bir zamansa zamandir; degilse sohbet adinin parcasi
 * sayilir. Eski kayitlardaki uzun etiketler de boyle okunur.
 */
function teamsLinkEtiketBol(etiket) {
  const bolum = String(etiket || "")
    .split(TEAMSLINK_AYRAC.trim())
    .map((parca) => parca.trim())
    .filter((parca) => parca !== "");
  if (!bolum.length) return { baslik: "", sohbet: "", zaman: "" };
  const zamanli = bolum.length > 1 && teamsLinkZamanCoz(bolum[bolum.length - 1]) !== null;
  const orta = bolum.slice(1, zamanli ? bolum.length - 1 : bolum.length);
  return {
    baslik: bolum[0],
    sohbet: orta.join(TEAMSLINK_AYRAC),
    zaman: zamanli ? bolum[bolum.length - 1] : "",
  };
}

/**
 * Etiketi cipe sigdirir.
 *
 * Sirayla: gonderen basligi ada iner, gondereni tekrar eden sohbet adi
 * duser, hala uzunsa sohbet adi tamamen gider, en son metin kirpilir. Veri
 * DEGISMEZ: bu yalnizca cizimdir, belirtec kayitta oldugu gibi kalir.
 */
function teamsLinkEtiketKisalt(etiket, sinir) {
  const limit = sinir || TEAMSLINK_ETIKET_SINIRI;
  const bolum = teamsLinkEtiketBol(etiket);
  let kisa = teamsLinkEtiketKur(bolum.baslik, bolum.sohbet, bolum.zaman);
  if (kisa.length > limit && bolum.sohbet) {
    kisa = teamsLinkEtiketKur(bolum.baslik, "", bolum.zaman);
  }
  if (kisa.length <= limit) return kisa;
  return kisa.slice(0, Math.max(1, limit - 1)).trim() + "…";
}

/** On satirdaki sohbet adi: "| <sohbet> sohbetinde gönderildi" arasi. */
function teamsLinkSohbetCoz(onSatir) {
  const ham = String(onSatir || "");
  const turkce = ham.match(/\|\s*(.+?)\s+(?:sohbetinde|kanalında)\b/i);
  if (turkce) return teamsLinkEtiketTemiz(turkce[1]);
  const ingilizce = ham.match(/\|\s*(.+?)\s+(?:chat|conversation|channel)\b/i);
  if (ingilizce) return teamsLinkEtiketTemiz(ingilizce[1]);
  return "";
}

/** Kanal mesajinda sohbet adi parametrelerden gelir: "<takım> / <kanal>". */
function teamsLinkKanalAdi(parametreler) {
  const takim = teamsLinkEtiketTemiz((parametreler || {}).teamName);
  const kanal = teamsLinkEtiketTemiz((parametreler || {}).channelName);
  if (takim && kanal) return `${takim} / ${kanal}`;
  return takim || kanal || "";
}

/**
 * Bir baglanti (ve varsa on satiri) -> belirtec parcalari.
 *
 * Doner: `{ad, sohbet, zaman, url, etiket, belirtec}`.
 */
function teamsLinkCozumle(url, onSatir) {
  const adres = String(url || "").trim();
  const yol = teamsLinkYol(adres);
  const parametreler = teamsLinkParametreler(adres);
  const baslik = teamsLinkBaslikCoz(onSatir);
  const ad = teamsLinkAdKirp(baslik) || TEAMSLINK_ADSIZ;
  const sohbet = teamsLinkSohbetCoz(onSatir) || teamsLinkKanalAdi(parametreler);
  const an = teamsLinkZamanCoz(onSatir) || teamsLinkKimlikZamani(yol.mesaj);
  const zaman = teamsLinkZamanYaz(an);

  // Sohbet adi yalnizca gondereni tekrar etmiyorsa yazilir.
  const etiket = teamsLinkEtiketKur(baslik || ad, sohbet, zaman);
  return {
    ad: ad,
    sohbet: sohbet,
    zaman: zaman,
    url: adres,
    etiket: etiket,
    belirtec: `[[teams: ${etiket}|${adres}]]`,
  };
}

/** Bir satir Teams'in kopyaladigi on satira benziyor mu? */
function teamsLinkOnSatirMi(satir) {
  const ham = String(satir || "").trim();
  if (!ham) return false;
  if (TEAMSLINK_ON_SATIR.test(ham)) return true;
  // Dil taninmadiysa: icinde cozulebilen bir gonderme zamani varsa yeter.
  return teamsLinkZamanCoz(ham) !== null;
}

/**
 * Panodaki metni alana yazilacak hale getirir.
 *
 * Yalnizca Teams mesaj bloklari belirtece doner; panodaki baska metin AYNEN
 * kalir. Blok = (varsa) on satir + bos satirlar + derin baglanti.
 */
function teamsLinkYapistir(pano) {
  const metin = String(pano || "");
  if (!metin) return metin;
  const parcalar = [];
  let imlec = 0;
  let bulunan;
  TEAMSLINK_ADRES.lastIndex = 0;
  while ((bulunan = TEAMSLINK_ADRES.exec(metin)) !== null) {
    let adres = bulunan[0];
    const kirpilan = adres.replace(TEAMSLINK_SON_NOKTALAMA, "");
    let son = bulunan.index + kirpilan.length;
    adres = kirpilan;

    // Baglantinin onunde duran bosluklari ve on satiri geri sar.
    let bas = bulunan.index;
    let arka = bas;
    while (arka > 0 && /\s/.test(metin[arka - 1])) arka--;
    const satirBasi = metin.lastIndexOf("\n", Math.max(arka - 1, 0)) + 1;
    const onSatir = arka > satirBasi ? metin.slice(satirBasi, arka) : "";
    let basligi = "";
    if (onSatir && teamsLinkOnSatirMi(onSatir)) {
      basligi = onSatir;
      bas = satirBasi;
    }

    if (bas < imlec) {
      // Onceki blokla cakisti: bu baglantiyi oldugu gibi birak.
      continue;
    }
    parcalar.push(metin.slice(imlec, bas));
    parcalar.push(teamsLinkCozumle(adres, basligi).belirtec);
    imlec = son;
    TEAMSLINK_ADRES.lastIndex = son;
  }
  if (!parcalar.length) return metin;
  parcalar.push(metin.slice(imlec));
  return parcalar.join("");
}

// --- belirteci metinden silme -------------------------------------------

/**
 * Belirtecleri metinden temizler (Excel, e-posta ve Teams govdeleri).
 *
 * Cevresindeki bosluk duzgun kalir: "önce [[teams:…]] sonra" -> "önce sonra",
 * kendi satirinda duran belirtec satiriyla birlikte gider. Python esi
 * `app/teamslink.temizle`; iki taraf ayni sonucu verir.
 */
function teamsLinkTemizle(metin) {
  const ham = String(metin === null || metin === undefined ? "" : metin);
  if (!ham) return "";
  const parcalar = [];
  let imlec = 0;
  let bulunan;
  TEAMSLINK_BELIRTEC.lastIndex = 0;
  while ((bulunan = TEAMSLINK_BELIRTEC.exec(ham)) !== null) {
    let bas = bulunan.index;
    let son = bas + bulunan[0].length;
    while (bas > 0 && (ham[bas - 1] === " " || ham[bas - 1] === "\t")) bas--;
    while (son < ham.length && (ham[son] === " " || ham[son] === "\t")) son++;
    const sol = bas > 0 ? ham[bas - 1] : "";
    const sag = son < ham.length ? ham[son] : "";
    let ayirici = " ";
    if (sol === "\n" && sag === "\n") {
      // Belirtec kendi satirindaydi: satir tamamen gider.
      son++;
      ayirici = "";
    } else if (sol === "" || sag === "" || sol === "\n" || sag === "\n") {
      ayirici = "";
    }
    // Iki belirtec arasinda metin kalmadi: ayirici bir kez konur.
    if (bas <= imlec) ayirici = "";
    parcalar.push(bas > imlec ? ham.slice(imlec, bas) : "");
    parcalar.push(ayirici);
    imlec = son;
    TEAMSLINK_BELIRTEC.lastIndex = son;
  }
  if (!parcalar.length) return ham;
  parcalar.push(ham.slice(imlec));
  return parcalar.join("");
}

// --- gosterim: belirtec -> cip, baglanti -> <a> --------------------------

/**
 * Metni gosterim parcalarina ayirir.
 *
 * Parca: `{tur: "metin"|"teams"|"bag", metin, url, ad, sohbet, zaman}`.
 */
function teamsLinkParcala(metin) {
  const ham = String(metin === null || metin === undefined ? "" : metin);
  const parcalar = [];
  if (!ham) return parcalar;

  const duzMetin = (parca) => {
    if (!parca) return;
    let imlec = 0;
    let bulunan;
    TEAMSLINK_DUZ_ADRES.lastIndex = 0;
    while ((bulunan = TEAMSLINK_DUZ_ADRES.exec(parca)) !== null) {
      const adres = bulunan[0].replace(TEAMSLINK_SON_NOKTALAMA, "");
      if (bulunan.index > imlec) {
        parcalar.push({ tur: "metin", metin: parca.slice(imlec, bulunan.index) });
      }
      parcalar.push({ tur: "bag", metin: adres, url: adres });
      imlec = bulunan.index + adres.length;
      TEAMSLINK_DUZ_ADRES.lastIndex = imlec;
    }
    if (imlec < parca.length) parcalar.push({ tur: "metin", metin: parca.slice(imlec) });
  };

  let imlec = 0;
  let bulunan;
  TEAMSLINK_BELIRTEC.lastIndex = 0;
  while ((bulunan = TEAMSLINK_BELIRTEC.exec(ham)) !== null) {
    duzMetin(ham.slice(imlec, bulunan.index));
    // Kayittaki etiket ne kadar uzun olursa olsun cipe kisaltilmis hali
    // girer; tam hali `title`'da durur, veri hic degismez.
    const tam = String(bulunan[1] || "").trim();
    const bolum = teamsLinkEtiketBol(tam);
    parcalar.push({
      tur: "teams",
      metin: bulunan[0],
      url: String(bulunan[2] || "").trim(),
      ad: teamsLinkAdKirp(bolum.baslik) || TEAMSLINK_ADSIZ,
      sohbet: bolum.sohbet,
      zaman: bolum.zaman,
      tam: tam,
      etiket: teamsLinkEtiketKisalt(tam),
    });
    imlec = bulunan.index + bulunan[0].length;
    TEAMSLINK_BELIRTEC.lastIndex = imlec;
  }
  duzMetin(ham.slice(imlec));
  return parcalar;
}

/** Cipin sol tarafindaki balon: marka logosu degil, kendi cizdigimiz ikon. */
function teamsLinkSimge() {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("class", "teams-msg-icon");
  svg.setAttribute("viewBox", "0 0 16 16");
  svg.setAttribute("aria-hidden", "true");
  const yol = document.createElementNS("http://www.w3.org/2000/svg", "path");
  yol.setAttribute(
    "d",
    "M2.5 3.2h11v7.1h-6l-3.2 2.6v-2.6H2.5zM5 6h6M5 8h4"
  );
  svg.appendChild(yol);
  return svg;
}

/**
 * Baglantiyi acar: once `msteams:`, olmazsa tarayici.
 *
 * `app/teams.py`'deki kalibin tarayici tarafi: uygulama protokolu adresi
 * isletim sistemine verir, Teams kurulu degilse hicbir sey olmaz ve kisa bir
 * gecikmenin ardindan `https` surumu yeni sekmede acilir.
 */
function teamsLinkAc(url) {
  const uygulama = teamsLinkUygulamaAdresi(url);
  const web = teamsLinkWebAdresi(url);
  let acildi = false;
  if (uygulama) {
    try {
      window.location.href = uygulama;
      acildi = true;
    } catch (err) {
      acildi = false;
    }
  }
  if (!web) return acildi;
  // Sayfa gizlenmediyse Teams acilmamistir: tarayici yedegi devreye girer.
  setTimeout(() => {
    if (document.hidden) return;
    window.open(web, "_blank", "noopener");
  }, TEAMSLINK_YEDEK_MS);
  return acildi;
}

/**
 * Tek bir belirtec parcasinin cipi.
 *
 * `secenekler.atomik` verilirse cip bir DUZENLEYICININ icinde duracaktir:
 * icine imlec girmez (`contenteditable="false"`), surtuklenmez ve belirtecin
 * kendisi `data-teams` icinde tasinir (bkz. zenginalan.js).
 * `secenekler.tik` cipe basildiginda ne olacagini degistirir.
 */
function teamsLinkCip(parca, secenekler) {
  const opt = secenekler || {};
  const web = teamsLinkWebAdresi(parca.url) || parca.url;
  const cip = document.createElement("a");
  cip.className = "teams-msg";
  cip.setAttribute("href", web);
  // Tam bilgi burada: kisaltilan baslik, sohbet adi ve zaman.
  cip.setAttribute("title", parca.tam || parca.etiket || TEAMSLINK_ADSIZ);
  if (opt.atomik) {
    cip.setAttribute("contenteditable", "false");
    cip.setAttribute("draggable", "false");
    cip.setAttribute("data-teams", parca.metin || `[[teams: ${parca.tam}|${parca.url}]]`);
  }
  cip.appendChild(teamsLinkSimge());
  const yazi = document.createElement("span");
  yazi.className = "teams-msg-text";
  yazi.textContent = parca.etiket;
  cip.appendChild(yazi);
  cip.addEventListener("click", (event) => {
    event.preventDefault();
    // Hucre/kart tiklamasi duzenleyiciyi acmasin.
    event.stopPropagation();
    if (typeof opt.tik === "function") opt.tik(event, cip, parca);
    else teamsLinkAc(parca.url);
  });
  return cip;
}

/**
 * Belirtecleri kisa etiketleriyle degistirir: `title`, tooltip, kisa ozet.
 *
 * Cip cizilemeyen yerlerde (bir `title` niteligi duz metindir) ham belirtec
 * gorunmesin diye. Metinden SILMEZ, bu is `teamsLinkTemizle`nin.
 */
function teamsLinkDuzMetin(metin) {
  const ham = String(metin === null || metin === undefined ? "" : metin);
  if (!ham) return "";
  TEAMSLINK_BELIRTEC.lastIndex = 0;
  return ham.replace(TEAMSLINK_BELIRTEC, (hepsi, etiket) => teamsLinkEtiketKisalt(etiket));
}

/**
 * Salt-okunur gosterim: metin DUGUM olarak basilir, HTML olarak DEGIL.
 *
 * Icerikteki `<script>` yazisi da metin kalir; tiklanabilir olan yalnizca
 * bizim urettigimiz `a` elemanlaridir.
 */
function teamsLinkCiz(metin) {
  const kutu = document.createDocumentFragment();
  teamsLinkParcala(metin).forEach((parca) => {
    if (parca.tur === "teams") {
      kutu.appendChild(teamsLinkCip(parca));
      return;
    }
    if (parca.tur === "bag") {
      const bag = document.createElement("a");
      bag.className = "text-link";
      bag.setAttribute("href", parca.url);
      bag.setAttribute("target", "_blank");
      bag.setAttribute("rel", "noopener");
      bag.textContent = parca.metin;
      bag.addEventListener("click", (event) => event.stopPropagation());
      kutu.appendChild(bag);
      return;
    }
    kutu.appendChild(document.createTextNode(parca.metin));
  });
  return kutu;
}

/** Bir dugumun icini salt-okunur gosterimle doldurur. */
function teamsLinkDoldur(dugum, metin) {
  if (!dugum) return dugum;
  while (dugum.firstChild) dugum.removeChild(dugum.firstChild);
  dugum.appendChild(teamsLinkCiz(metin));
  return dugum;
}

// --- yapistirma kancasi --------------------------------------------------

/** Metni imlecin oldugu yere yazar; tarayicinin geri alma yigini korunur. */
function teamsLinkYaz(alan, metin) {
  let oldu = false;
  try {
    oldu = document.execCommand("insertText", false, metin);
  } catch (err) {
    oldu = false;
  }
  if (!oldu) {
    const deger = alan.value || "";
    const bas = typeof alan.selectionStart === "number" ? alan.selectionStart : deger.length;
    const son = typeof alan.selectionEnd === "number" ? alan.selectionEnd : deger.length;
    alan.value = deger.slice(0, bas) + metin + deger.slice(son);
    const yeni = bas + metin.length;
    try {
      alan.setSelectionRange(yeni, yeni);
    } catch (err) {
      // Tarayici desteklemiyorsa imlecin yeri onemli degil.
    }
  }
  try {
    if (typeof Event === "function") {
      alan.dispatchEvent(new Event("input", { bubbles: true }));
    }
  } catch (err) {
    // Olay gonderilemedi; alanin degeri yine de yerinde.
  }
}

/**
 * Blok DISI satir sonlarini boslukla degistirir.
 *
 * Yalnizca tek satirlik hedeflerde (grid hucresi, tek satirlik yerel alan)
 * kullanilir: belirtecin kendisi zaten tek satirdir, geri kalan metindeki
 * (kullanicinin ayni yapistirmada getirdigi baska satirlarin) satir sonlari
 * boslukga iner ki deger tek satirlik `input`'a duzgun sigsin.
 */
function teamsLinkTekSatiraIndir(metin) {
  return String(metin || "").replace(/\r\n|\r|\n/g, " ");
}

/**
 * Bir metin alanina Teams yapistirma cevirisini takar.
 *
 * Ayni alana ikinci kez cagrilmak zararsizdir (pencere yeniden cizilince
 * kanca iki kez takilmasin). Panoda Teams baglantisi yoksa olaya hic
 * karisilmaz: tarayici kendi yapistirmasini yapar.
 *
 * `secenekler.tekSatir` verilirse (grid hucresi gibi tek satirlik alanlar)
 * donen metindeki blok disi satir sonlari boslukga indirgenir.
 */
function attachTeamsLink(alan, secenekler) {
  if (!alan || alan.teamsLinkBagli) return alan;
  alan.teamsLinkBagli = true;
  const tekSatir = !!(secenekler && secenekler.tekSatir);
  alan.addEventListener("paste", (event) => {
    const pano = event.clipboardData ? event.clipboardData.getData("text/plain") : "";
    if (!pano) return;
    let cevrilmis = teamsLinkYapistir(pano);
    if (cevrilmis === pano) return;
    if (tekSatir) cevrilmis = teamsLinkTekSatiraIndir(cevrilmis);
    event.preventDefault();
    teamsLinkYaz(alan, cevrilmis);
  });
  return alan;
}
