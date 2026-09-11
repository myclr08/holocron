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
