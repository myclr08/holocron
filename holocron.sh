#!/usr/bin/env bash
# Holocron baslatici (Linux/macOS): gerekirse sanal ortam kurar, sonra uygulamayi acar.
set -euo pipefail

# readlink -f her yerde yok; betigin bulundugu klasore tasinmanin tasinabilir yolu.
cd "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)"

PYTHON_BIN="${PYTHON_BIN:-python3}"
VENV_DIR=".venv"
VENV_PY="$VENV_DIR/bin/python"

if [ ! -x "$VENV_PY" ]; then
  echo "[holocron] Sanal ortam kuruluyor..."
  "$PYTHON_BIN" -m venv "$VENV_DIR"
  "$VENV_PY" -m pip install --upgrade pip >/dev/null

  if [ -d "wheels" ]; then
    # Cevrimdisi kurulum: paketler yanimizda geldiyse agi hic kullanma.
    echo "[holocron] Bagimliliklar wheels/ klasorunden kuruluyor..."
    "$VENV_PY" -m pip install --no-index --find-links wheels -r requirements.txt
  else
    echo "[holocron] Bagimliliklar indiriliyor..."
    "$VENV_PY" -m pip install -r requirements.txt
  fi
fi

exec "$VENV_PY" -m app "$@"
