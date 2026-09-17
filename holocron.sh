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

if [ ! -x "$VENV_PY" ]; then
  echo "[holocron] Sanal ortam kuruluyor..."
  "$PYTHON_BIN" -m venv "$VENV_DIR"
  "$VENV_PY" -m pip install --upgrade pip >/dev/null

  if [ -d "wheels" ]; then
    # Cevrimdisi kurulum: paketler yanimizda geldiyse agi hic kullanma.
    echo "[holocron] Bagimliliklar wheels/ klasorunden kuruluyor..."
    if ! "$VENV_PY" -m pip install --no-index --find-links wheels -r requirements.txt; then
      # Tekerlekler baska bir Python surumu icin olabilir; agdan denenir.
      echo "[holocron] Cevrimdisi kurulum olmadi, agdan deneniyor..."
      "$VENV_PY" -m pip install -r requirements.txt
    fi
  else
    echo "[holocron] Bagimliliklar indiriliyor..."
    "$VENV_PY" -m pip install -r requirements.txt
  fi
fi

# Modul kipi calisma dizinine bagimli; giris dosyasi tam yoluyla cagriliyor ki
# betik nereden calistirilirsa calistirilsin paket bulunabilsin.
export PYTHONPATH="$HERE${PYTHONPATH:+:$PYTHONPATH}"
exec "$VENV_PY" "$ENTRY" "$@"
