#!/usr/bin/env bash
set -euo pipefail

APP_ROOT="/home/kalyan/Desktop/GenAI_Projects/Plant-LMS/Backend"

if [[ -z "${DATABASE_URL:-}" ]]; then
  echo "DATABASE_URL is not set. Export it before running this script."
  exit 1
fi

PASS_COUNT=0
FAIL_COUNT=0

check_file() {
  local file_path="$1"
  local label="$2"
  if [[ -f "$file_path" ]]; then
    echo "[PASS] $label"
    PASS_COUNT=$((PASS_COUNT+1))
  else
    echo "[FAIL] $label"
    FAIL_COUNT=$((FAIL_COUNT+1))
  fi
}

check_file "$APP_ROOT/app/api/v1/endpoints/ingestion.py" "ingestion endpoint present"
check_file "$APP_ROOT/app/services/ingestion_service.py" "ingestion service present"
check_file "$APP_ROOT/app/services/document_extraction.py" "extraction service present"
check_file "$APP_ROOT/app/services/chunking_service.py" "chunking service present"
check_file "$APP_ROOT/app/integrations/embedding_client.py" "embedding client present"
check_file "$APP_ROOT/app/integrations/llm_client.py" "llm client present"
check_file "$APP_ROOT/app/integrations/file_storage.py" "file storage present"
check_file "$APP_ROOT/app/models/chunk.py" "chunk model present"
check_file "$APP_ROOT/app/models/mcq.py" "mcq model present"
check_file "$APP_ROOT/tests/test_ingestion_routes.py" "ingestion route tests present"

cd "$APP_ROOT"
if venv/bin/python - <<'PYEOF' >/dev/null 2>&1
from app.main import app
from app.api.v1.endpoints import ingestion
from app.services.ingestion_service import IngestionService
from app.services.document_extraction import extract_text_from_file
from app.services.chunking_service import chunk_document_text
print(app, ingestion, IngestionService, extract_text_from_file, chunk_document_text)
PYEOF
then
  echo "[PASS] ingestion modules import successfully"
  PASS_COUNT=$((PASS_COUNT+1))
else
  echo "[FAIL] ingestion modules import successfully"
  FAIL_COUNT=$((FAIL_COUNT+1))
fi

if venv/bin/python -m pytest tests/test_ingestion_routes.py -q >/dev/null 2>&1; then
  echo "[PASS] pytest suite passed"
  PASS_COUNT=$((PASS_COUNT+1))
else
  echo "[FAIL] pytest suite failed"
  FAIL_COUNT=$((FAIL_COUNT+1))
fi

echo "Summary: PASS=$PASS_COUNT FAIL=$FAIL_COUNT"
if [[ $FAIL_COUNT -gt 0 ]]; then
  exit 1
fi
