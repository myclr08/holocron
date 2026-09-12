// Ayarlar -> "Sefer" kartı: XP kuralları, odak bütçesi, koruma durumu.
//
// settings.js'ten ayrı durur: kendi ucu (`/api/campaign/rules`) ve kendi
// listesi var. Kendi DOMContentLoaded dinleyicisini kurar.

const campaignSettings = { rules: [], focusHours: 8 };

function campaignStatusBox() {
  return document.getElementById("campaign-status");
}

function campaignRuleRow(rule) {
  const row = document.createElement("div");
  row.className = "rule-line";

  const toggle = document.createElement("label");
  toggle.className = "checkbox";
  const check = document.createElement("input");
  check.type = "checkbox";
  check.checked = !!rule.enabled;
  check.dataset.kind = rule.kind;
  check.title = "Kapalı kural hiç puan vermez";
  const text = document.createElement("span");
  text.textContent = rule.label;
  toggle.appendChild(check);
  toggle.appendChild(text);

  const source = document.createElement("span");
  source.className = "rule-source";
  source.textContent = rule.source_label;

  const points = document.createElement("input");
  points.type = "number";
  points.className = "rule-points";
  points.min = "0";
  points.max = "1000";
  points.step = "1";
  points.value = String(rule.points);
  points.dataset.kind = rule.kind;
  points.setAttribute("aria-label", rule.label + " puanı");

  row.appendChild(toggle);
  row.appendChild(source);
  row.appendChild(points);
  return row;
}

function renderCampaignRules() {
  const box = document.getElementById("campaign-rules");
  while (box.firstChild) box.removeChild(box.firstChild);
  campaignSettings.rules.forEach((rule) => box.appendChild(campaignRuleRow(rule)));
  document.getElementById("campaign-focus").value = String(campaignSettings.focusHours);
}

async function loadCampaignRules() {
  const data = await api("/api/campaign/rules");
  campaignSettings.rules = data.rules || [];
  campaignSettings.focusHours = data.focus_hours || 8;
  renderCampaignRules();
  document.getElementById("campaign-grace").textContent = data.grace_used
    ? `Güç koruması ${data.grace_month} ayında harcandı; gelecek ay yenilenir.`
    : "Güç koruması hazır: ayda bir kaçırılan iş gününü affeder.";
}

async function saveCampaignRules() {
  const rules = [];
  document.querySelectorAll("#campaign-rules .rule-points").forEach((box) => {
    const check = document.querySelector(
      `#campaign-rules input[type="checkbox"][data-kind="${box.dataset.kind}"]`
    );
    rules.push({
      kind: box.dataset.kind,
      points: Number(box.value),
      enabled: check ? check.checked : true,
    });
  });
  const payload = {
    rules: rules,
    focus_hours: Number(document.getElementById("campaign-focus").value) || 8,
  };
  try {
    const data = await api("/api/campaign/rules", {
      method: "PUT",
      body: JSON.stringify(payload),
    });
    campaignSettings.rules = data.rules || [];
    campaignSettings.focusHours = data.focus_hours || 8;
    renderCampaignRules();
    setStatus(campaignStatusBox(), "Sefer kuralları kaydedildi.", "ok");
  } catch (err) {
    setStatus(campaignStatusBox(), err.message, "error");
  }
}

document.addEventListener("DOMContentLoaded", () => {
  document.getElementById("campaign-rules-save").addEventListener("click", saveCampaignRules);
  loadCampaignRules().catch((err) => setStatus(campaignStatusBox(), err.message, "error"));
});
