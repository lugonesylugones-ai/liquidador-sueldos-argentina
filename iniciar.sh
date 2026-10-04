#!/bin/sh
# Arranca el liquidador en Mac o Linux: la primera vez instala lo necesario.
set -e
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  echo "Preparando el entorno por primera vez..."
  python3 -m venv .venv
fi
if ! cmp -s requirements.txt .venv/requirements.txt; then
  echo "Instalando dependencias..."
  .venv/bin/python -m pip install -q -r requirements.txt
  cp requirements.txt .venv/requirements.txt
fi
echo "Liquidador andando en http://127.0.0.1:5000 (Ctrl+C para cerrarlo)"
( sleep 3; (open http://127.0.0.1:5000 || xdg-open http://127.0.0.1:5000) >/dev/null 2>&1 ) &
exec .venv/bin/python -m flask --app backend.app:create_app run
