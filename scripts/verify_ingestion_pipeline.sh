#!/usr/bin/env bash
# Verify the RAG ingestion pipeline is wired correctly.
# T-1 fix: references the real module layout (app/clients, app/storage, ...) and
# runs the actual pipeline test suite instead of non-existent files.
set -euo pipefail

APP_ROOT="${APP_ROOT:-/home/kalyan/Desktop/GenAI_Projects/Plant-LMS/Backend}"
PYTHON_BIN="${PYTHON_BIN:-$APP_ROOT/venv/bin/python}"

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
check_file "$APP_ROOT/app/services/extraction_service.py" "extraction service present"
check_file "$APP_ROOT/app/services/chunking_service.py" "chunking service present"
check_file "$APP_ROOT/app/clients/embedding_client.py" "embedding client present"
check_file "$APP_ROOT/app/clients/llm_client.py" "llm client present"
check_file "$APP_ROOT/app/storage/file_storage.py" "file storage present"
check_file "$APP_ROOT/app/models/chunk.py" "chunk model present"
check_file "$APP_ROOT/app/models/mcq.py" "mcq model present"
check_file "$APP_ROOT/tests/test_pipeline.py" "pipeline tests present"

cd "$APP_ROOT"
if "$PYTHON_BIN" - <<'PYEOF' >/dev/null 2>&1
from app.main import app
from app.api.v1.endpoints import ingestion
from app.services.ingestion_service import IngestionService
from app.services.extraction_service import ExtractionService
from app.services.chunking_service import ChunkingService
from app.clients.embedding_client import EmbeddingClient
from app.clients.llm_client import LLMClient
print(app, ingestion, IngestionService, ExtractionService, ChunkingService, EmbeddingClient, LLMClient)
PYEOF
then
  echo "[PASS] ingestion modules import successfully"
  PASS_COUNT=$((PASS_COUNT+1))
else
  echo "[FAIL] ingestion modules import successfully"
  FAIL_COUNT=$((FAIL_COUNT+1))
fi

if "$PYTHON_BIN" -m pytest tests -q >/dev/null 2>&1; then
  echo "[PASS] pipeline test suite passed"
  PASS_COUNT=$((PASS_COUNT+1))
else
  echo "[FAIL] pipeline test suite failed"
  FAIL_COUNT=$((FAIL_COUNT+1))
fi

echo "Summary: PASS=$PASS_COUNT FAIL=$FAIL_COUNT"
if [[ $FAIL_COUNT -gt 0 ]]; then
  exit 1
fi
