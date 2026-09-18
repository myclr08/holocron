#!/usr/bin/env bash
# Holocron baslatici (Linux/macOS): gerekirse sanal ortam kurar, sonra uygulamayi acar.
set -euo pipefail

# readlink -f her yerde yok; betigin bulundugu klasore tasinmanin tasinabilir yolu.
HERE="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)"
cd "$HERE"

PYTHON_BIN="${PYTHON_BIN:-python3}"
VENV_DIR=".venv"
VENV_PY="$VENV_DIR/bin/python"
ENTRY="$HERE/holocron_run.py"
PORT_FILE="$HERE/holocron.port"

# /api/health yoklamasi: 0 donerse o portta ayakta bir ornek var.
probe_health() {
  url="http://127.0.0.1:$1/api/health"
  if command -v curl >/dev/null 2>&1; then
    curl -fsS --max-time 5 -o /dev/null "$url" >/dev/null 2>&1
    return $?
  fi
  if command -v wget >/dev/null 2>&1; then
    wget -q -T 5 -O /dev/null "$url" >/dev/null 2>&1
    return $?
  fi
  return 1  # yoklayacak arac yok: koruma atlanir, normal baslatma surer
}

# Cift baslatma korumasi: zaten calisan bir ornek varsa yeni surec acma,
# yalnizca tarayiciyi o adrese getir. Arguman verildiyse atlanir (--port ya da
# --console veren kullanici bilerek ikinci ornek istiyordur).
if [ "$#" -eq 0 ] && [ -f "$PORT_FILE" ]; then
  RUNNING_PORT="$(tr -dc '0-9' < "$PORT_FILE" 2>/dev/null || true)"
  if [ -n "$RUNNING_PORT" ] && probe_health "$RUNNING_PORT"; then
    RUNNING_URL="http://127.0.0.1:$RUNNING_PORT/"
    echo "[holocron] Zaten calisiyor: $RUNNING_URL"
    if command -v xdg-open >/dev/null 2>&1; then
      xdg-open "$RUNNING_URL" >/dev/null 2>&1 || true
    elif command -v open >/dev/null 2>&1; then
      open "$RUNNING_URL" >/dev/null 2>&1 || true
    fi
    exit 0
  fi
fi

# requirements.txt'in ozeti: kurulan liste ile acilistaki liste ayni mi?
REQ_STAMP="$VENV_DIR/holocron-req.sha"

req_hash() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum requirements.txt 2>/dev/null | cut -d " " -f 1
  elif command -v shasum >/dev/null 2>&1; then
    shasum -a 256 requirements.txt 2>/dev/null | cut -d " " -f 1
  fi
  return 0
}

# Yanimizda gelen tekerlekler: wheels/ tam liste, whisper-wheels/ yaziya
# dokme paketi. Bos donerse kurulum agdan gider.
find_links() {
  links=""
  if [ -d "wheels" ]; then links="$links --find-links wheels"; fi
  if [ -d "whisper-wheels" ]; then links="$links --find-links whisper-wheels"; fi
  if [ -n "$links" ]; then printf '%s' "--no-index$links"; fi
  return 0
}

write_stamp() {
  hash_now="$(req_hash)"
  if [ -n "$hash_now" ]; then printf '%s\n' "$hash_now" > "$REQ_STAMP"; fi
  return 0
}

# Her acilista: requirements.txt degistiyse (surum yukseltmesi, sonradan
# eklenen faster-whisper) bagimliliklar tazelenir. Kurulum dusse bile
# uygulama ACILIR; eksik paket yalnizca ilgili ozelligi kapatir.
sync_deps() {
  hash_now="$(req_hash)"
  if [ -z "$hash_now" ]; then return 0; fi
  hash_old=""
  if [ -f "$REQ_STAMP" ]; then hash_old="$(cat "$REQ_STAMP" 2>/dev/null || true)"; fi
  if [ "$hash_now" = "$hash_old" ]; then return 0; fi
  echo "[holocron] Bagimliliklar guncelleniyor..."
  links="$(find_links)"
  if [ -n "$links" ] && "$VENV_PY" -m pip install $links -r requirements.txt; then
    write_stamp
    return 0
  fi
  if "$VENV_PY" -m pip install -r requirements.txt; then
    write_stamp
    return 0
  fi
  echo "[holocron] UYARI: bagimliliklar guncellenemedi; eksik paket yalnizca ilgili ozelligi kapatir."
  return 0
}

if [ ! -x "$VENV_PY" ]; then
  echo "[holocron] Sanal ortam kuruluyor..."
  "$PYTHON_BIN" -m venv "$VENV_DIR"
  "$VENV_PY" -m pip install --upgrade pip >/dev/null

  links="$(find_links)"
  if [ -n "$links" ]; then
    # Cevrimdisi kurulum: paketler yanimizda geldiyse agi hic kullanma.
    echo "[holocron] Bagimliliklar yerel tekerleklerden kuruluyor..."
    if ! "$VENV_PY" -m pip install $links -r requirements.txt; then
      # Tekerlekler baska bir Python surumu icin olabilir; agdan denenir.
      echo "[holocron] Cevrimdisi kurulum olmadi, agdan deneniyor..."
      "$VENV_PY" -m pip install -r requirements.txt
    fi
  else
    echo "[holocron] Bagimliliklar indiriliyor..."
    "$VENV_PY" -m pip install -r requirements.txt
  fi
  write_stamp
else
  sync_deps
fi

# Modul kipi calisma dizinine bagimli; giris dosyasi tam yoluyla cagriliyor ki
# betik nereden calistirilirsa calistirilsin paket bulunabilsin.
export PYTHONPATH="$HERE${PYTHONPATH:+:$PYTHONPATH}"
exec "$VENV_PY" "$ENTRY" "$@"
