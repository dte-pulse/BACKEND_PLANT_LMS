#!/usr/bin/env bash
set -euo pipefail
source venv/bin/activate
celery -A app.celery_app.celery_app flower --port=5555
