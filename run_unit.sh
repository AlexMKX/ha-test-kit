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

docker run --rm \
  -v "${PROJECT_ROOT}:/work:ro" \
  -e AUTOQA_MODE=unit \
  -e AUTOQA_PYTEST_CONFIG="ha-test-kit/pyproject.toml" \
  -e AUTOQA_TEST_PATHS="${AUTOQA_TEST_PATHS:-}" \
  -e AUTOQA_PYTEST_ARGS="${AUTOQA_PYTEST_ARGS:-}" \
  autoqa-unit
