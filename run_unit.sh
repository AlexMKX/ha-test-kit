#!/usr/bin/env bash
set -euo pipefail

# Build the autoqa image and run unit tests.
# Optional overrides: AUTOQA_TEST_PATHS, AUTOQA_PYTEST_ARGS, PROJECT_ROOT

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="${PROJECT_ROOT:-$(cd "${SCRIPT_DIR}/.." && pwd)}"

docker build \
  -t autoqa-unit \
  -f "${SCRIPT_DIR}/Dockerfile.autoqa" \
  "${SCRIPT_DIR}"

mkdir -p "${PROJECT_ROOT}/.autoqa/coverage"

docker run --rm \
  -v "${PROJECT_ROOT}:/work:ro" \
  -v "${PROJECT_ROOT}/.autoqa:/work/.autoqa" \
  -e AUTOQA_MODE=unit \
  -e AUTOQA_PYTEST_CONFIG="ha-test-kit/pyproject.toml" \
  -e AUTOQA_TEST_PATHS="${AUTOQA_TEST_PATHS:-}" \
  -e AUTOQA_PYTEST_ARGS="${AUTOQA_PYTEST_ARGS:-}" \
  -e AUTOQA_LINT="${AUTOQA_LINT:-}" \
  -e AUTOQA_VULTURE="${AUTOQA_VULTURE:-}" \
  -e AUTOQA_COVERAGE="${AUTOQA_COVERAGE:-}" \
  autoqa-unit
