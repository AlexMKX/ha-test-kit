#!/usr/bin/env bash
set -euo pipefail

# Run E2E tests via Docker Compose (ha-test-kit harness).
# Optional overrides: HA_VERSION, AUTOQA_TEST_PATHS, AUTOQA_PYTEST_ARGS, PROJECT_ROOT

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="${PROJECT_ROOT:-$(cd "${SCRIPT_DIR}/.." && pwd)}"

cleanup() {
  docker compose \
    -f "${SCRIPT_DIR}/docker-compose.yml" \
    --project-directory "${PROJECT_ROOT}" \
    down -v || true
}

trap cleanup EXIT

docker compose \
  -f "${SCRIPT_DIR}/docker-compose.yml" \
  --project-directory "${PROJECT_ROOT}" \
  run --rm seed

docker compose \
  -f "${SCRIPT_DIR}/docker-compose.yml" \
  --project-directory "${PROJECT_ROOT}" \
  up --build --abort-on-container-exit --exit-code-from autoqa homeassistant autoqa
