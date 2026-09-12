// Ortak adres kutusu: çipler, adres defterinden tamamlama, ayrıştırma.
//
// Hem ana ekrandaki "E-posta ile gönder" penceresi hem Ayarlar'daki şablon
// kartı bunu kullanır; bu yüzden app.js'in yardımcılarına (h, clear) değil
// yalnız düz DOM'a dayanır.
//
// Saha hatası bu dosyayı doğurdu: adres kutusu metni **boşlukta** bölüyordu,
// tamamlama listesinden seçilen "Mustafa Erhan" iki çipe ("mustafa" / "erhan")
// düşüyor ve e-posta hiç girmiyordu. Kural artık tek: ayırıcı yalnızca
// `,` `;` ve satır sonudur, boşluk ASLA ayırıcı değildir.

// Ayırıcılar; boşluk bilerek yok.
const ADDRESS_SPLIT = /[,;\r\n]+/;
// "Ad Soyad <adres@example.com>" -- Outlook ve posta istemcilerinin biçimi.
const ADDRESS_ANGLE = /^\s*(.*?)\s*<\s*([^<>]+?)\s*>\s*$/;
const ADDRESS_SUGGEST_MS = 200;

/** Metni alıcılara ayırır: `{entries: [{name, email}], invalid: [metin]}`. */
function parseAddressText(text) {
  const entries = [];
  const invalid = [];
  String(text || "")
    .split(ADDRESS_SPLIT)
    .forEach((raw) => {
      const piece = raw.trim();
      if (!piece) return;
      const match = ADDRESS_ANGLE.exec(piece);
      const email = (match ? match[2] : piece).trim();
      let name = match ? match[1].trim().replace(/^["']|["']$/g, "") : "";
      // Adres bosluk tasiyamaz: bosluk ayirici olmadigi icin "a@x b@y" tek
      // parca kalir ve gecerli bir alici degildir.
      if (email.indexOf("@") < 0 || /\s/.test(email)) {
        invalid.push(piece);
        return;
      }
      if (name.toLowerCase() === email.toLowerCase()) name = "";
      entries.push({ name: name, email: email });
    });
  return { entries: entries, invalid: invalid };
}

/** Saklama biçimi: `;` ile ayrılmış `Ad <adres>` ya da düz adres. */
function formatAddresses(entries) {
  return (entries || [])
    .filter((item) => item && item.email)
    .map((item) => (item.name ? `${item.name} <${item.email}>` : item.email))
    .join("; ");
}

/** Sunucuya giden liste: yalnız adresler. */
function addressEmails(entries) {
  return (entries || []).filter((item) => item && item.email).map((item) => item.email);
}

function addressNode(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = text;
  return node;
}

/**
 * Çipli adres kutusu kurar.
 *
 * Dönen düğümde `entries` canlı listedir (`[{name, email}]`); `setText`,
 * `getText` ve `redraw` yardımcıları düğümün üstünde durur.
 */
function createAddressBox(options) {
  const settings = options || {};
  const entries = settings.entries || [];
  const box = addressNode("div", "address-box");
  const chips = addressNode("div", "address-chips");
  const input = document.createElement("input");
  input.type = "text";
  input.autocomplete = "off";
  input.placeholder = settings.placeholder || "ornek@example.com";
  if (settings.id) input.id = settings.id;
  const suggestBox = addressNode("div", "address-suggest");
  const errorLine = addressNode("p", "address-error");
  errorLine.hidden = true;

  const changed = () => {
    if (typeof settings.onChange === "function") settings.onChange(entries);
  };

  const warn = (bad) => {
    if (!bad || !bad.length) {
      errorLine.hidden = true;
      errorLine.textContent = "";
      return;
    }
    errorLine.textContent = `Geçersiz adres: ${bad.join(", ")} — e-posta adresi "@" içermeli.`;
    errorLine.hidden = false;
  };

  const draw = () => {
    chips.textContent = "";
    entries.forEach((entry, index) => {
      const chip = addressNode("span", "address-chip");
      // Gorunen metin ad, ipucu adres: "Kime kime gidiyor" bir bakista okunur.
      const label = addressNode("span", "", entry.name || entry.email);
      chip.title = entry.name ? `${entry.name} <${entry.email}>` : entry.email;
      const drop = addressNode("button", "", "×");
      drop.title = "Çıkar";
      drop.addEventListener("click", () => {
        entries.splice(index, 1);
        draw();
        changed();
      });
      chip.appendChild(label);
      chip.appendChild(drop);
      chips.appendChild(chip);
    });
  };

  /** Tek bir alıcı ekler (tamamlama listesinden seçim bu yolu kullanır). */
  const addEntry = (entry) => {
    const email = String((entry || {}).email || "").trim();
    if (!email || email.indexOf("@") < 0 || /\s/.test(email)) {
      warn([email || String((entry || {}).name || "")]);
      return false;
    }
    const marker = email.toLowerCase();
    if (!entries.some((item) => item.email.toLowerCase() === marker)) {
      entries.push({ name: String((entry || {}).name || "").trim(), email: email });
    }
    warn([]);
    draw();
    changed();
    return true;
  };

  /** Elle yazılan / yapıştırılan metni işler. */
  const addText = (value) => {
    const text = String(value || "").trim();
    if (!text) {
      warn([]);
      return;
    }
    const parsed = parseAddressText(text);
    parsed.entries.forEach((entry) => {
      const marker = entry.email.toLowerCase();
      if (!entries.some((item) => item.email.toLowerCase() === marker)) entries.push(entry);
    });
    input.value = "";
    suggestBox.textContent = "";
    warn(parsed.invalid);
    draw();
    changed();
  };

  let timer = null;
  const suggest = async () => {
    const typed = input.value.trim();
    suggestBox.textContent = "";
    if (typed.length < 2 || typeof api !== "function") return;
    try {
      const data = await api(`/api/contacts?q=${encodeURIComponent(typed)}&limit=8`);
      (data.contacts || []).forEach((contact) => {
        const button = addressNode(
          "button",
          "small",
          contact.name ? `${contact.name} · ${contact.email}` : contact.email
        );
        // mousedown'da odagi birakmiyoruz: yoksa `blur` once calisip yazilan
        // metni cip yapiyor ve tiklama hic gerceklesmiyordu.
        button.addEventListener("mousedown", (event) => event.preventDefault());
        button.addEventListener("click", () => {
          addEntry({ name: contact.name, email: contact.email });
          input.value = "";
          suggestBox.textContent = "";
        });
        suggestBox.appendChild(button);
      });
    } catch (err) {
      // Adres defteri okunamadi; elle yazmak yine calisir.
    }
  };

  input.addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(suggest, ADDRESS_SUGGEST_MS);
  });

  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === "Tab" || event.key === "," || event.key === ";") {
      if (!input.value.trim()) return;
      event.preventDefault();
      addText(input.value);
      return;
    }
    // Bos kutuda Backspace son cipi siler: posta istemcilerindeki alisilmis hal.
    if (event.key === "Backspace" && !input.value && entries.length) {
      entries.pop();
      draw();
      changed();
    }
  });

  input.addEventListener("blur", () => addText(input.value));

  box.appendChild(chips);
  box.appendChild(input);
  box.appendChild(suggestBox);
  box.appendChild(errorLine);

  box.entries = entries;
  box.input = input;
  box.redraw = draw;
  box.addEntry = addEntry;
  box.commit = () => addText(input.value);
  box.getText = () => formatAddresses(entries);
  box.setText = (text) => {
    entries.length = 0;
    parseAddressText(text).entries.forEach((entry) => entries.push(entry));
    warn([]);
    draw();
  };

  draw();
  return box;
}
