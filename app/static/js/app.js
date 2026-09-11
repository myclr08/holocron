// Kabuk ekrani. Gruplar ve kayit listesi sonraki asamada gelecek.
document.addEventListener("DOMContentLoaded", () => {
  bindShell();
  api("/api/health")
    .then((data) => {
      const el = document.getElementById("version");
      if (el) el.textContent = "s" + data.version;
    })
    .catch(() => {});

  api("/api/settings")
    .then((data) => {
      const el = document.getElementById("connection-hint");
      if (!el) return;
      const settings = data.settings || {};
      if (settings["jira.base_url"] && settings.secret_set) {
        el.textContent = "Baglanti ayarlari girilmis. Gruplar sonraki asamada eklenecek.";
      } else {
        el.innerHTML =
          'Once <a href="/settings">Ayarlar</a> ekranindan Jira baglantisini tanimlayin.';
      }
    })
    .catch(() => {});
});
