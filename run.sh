#!/bin/bash

cd "$(dirname "$0")"

echo "========================================"
echo "  NEXUS TRADING BOT - Startup"
echo "========================================"
echo ""

PY=python3
if [ -x ".venv/bin/python" ]; then
    PY=".venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
    PY=python3
elif command -v python >/dev/null 2>&1; then
    PY=python
fi

echo "Using $PY"

echo "[1] Installing dependencies..."
$PY -m pip install -r requirements.txt
if [ $? -ne 0 ]; then
    echo "Error installing dependencies"
    exit 1
fi

echo ""
echo "[2] Starting dashboard..."
echo "Dashboard: http://localhost:5000"
echo ""

$PY app.py
