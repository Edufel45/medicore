#!/usr/bin/env bash
# =====================================================
# MediCore — Render build script
# Runs on every deploy, before the app starts.
# =====================================================

set -o errexit   # exit on error
set -o pipefail  # propagate pipe errors
set -o nounset   # treat unset variables as errors

echo "▶ Installing dependencies…"
pip install --upgrade pip
pip install -r requirements.txt

echo "▶ Collecting static files…"
python manage.py collectstatic --no-input --clear

echo "▶ Applying migrations…"
python manage.py migrate --no-input

echo "▶ Seeding demo users…"
python manage.py seed_demo || echo "seed_demo skipped (not critical)"

echo "✅ Build complete."