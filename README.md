# ha-test-kit

`ha-test-kit` is a small, Docker-first test harness for this repository.

It is designed to make Home Assistant custom-component testing repeatable in CI and locally by providing:

- A **known Home Assistant runtime** (pinned version via Docker build arg).
- A single entrypoint (`python -m ha_test_kit.autoqa`) that can run:
  - **Unit tests** (regular pytest, no Docker / no HA process).
  - **E2E tests** against a real Home Assistant instance started in Docker.
- Automatic **HA configuration seeding** and **auth token provisioning** for E2E.

This README aims to be clear for both:

- **Humans**: how to run tests and where to put things.
- **Agents**: what invariants exist and how the harness expects tests to be structured.

---

## Goals and non-goals

### Goals

- Provide a reproducible, self-contained way to run tests in a clean environment.
- Make E2E tests practical without manually creating users/tokens in Home Assistant.
- Keep project code mounted read-only to avoid accidental mutations.
- Autodiscover “HA packages” YAML fragments from the repo and inject them into HA config.

### Non-goals

- This is not a full “Home Assistant devcontainer”.
- This does not attempt to emulate the full HA supervisor/add-ons environment.
- It is not meant to replace `pytest_homeassistant_custom_component` (you can enable that plugin if you want).

---

## How it works (high level)

The harness entrypoint is `ha_test_kit/autoqa.py`.

It supports two modes:

- **Unit mode** (`AUTOQA_MODE=unit`)
  - Runs `pytest` inside the container.
  - Automatically excludes E2E tests via `-m "not docker_e2e"`.

- **E2E mode** (`AUTOQA_MODE=e2e`)
  1. Seeds a Home Assistant config directory (`/ha_config`).
  2. Starts Home Assistant (via Docker Compose in this repo).
  3. Waits until HA API responds.
  4. Creates an onboarding user (or falls back to the login flow).
  5. Exchanges auth code for an access token.
  6. Creates a **long-lived access token** via websocket.
  7. Runs `pytest` with E2E marker via `-m docker_e2e`, and exports:
     - `HASS_LONG_LIVED_TOKEN` (for REST API calls)
     - `HA_BASE_URL`
     - `RUN_DOCKER_E2E=1`

---

## Repo conventions the harness relies on

### 1) HA packages autodiscovery

For E2E runs, HA is configured with:

- `packages: !include_dir_named packages`

`autoqa` will discover package YAML files and copy them into `/ha_config/packages/`.

Autodiscovery paths (relative to repo root mounted at `/work`):

- `tests/ha_packages/*.yaml`
- `custom_components/*/tests/ha_packages/*.yaml`

Use these package fragments to configure integrations/entities that your E2E tests need.

### 2) E2E tests are marked

Any test that requires a real HA running in Docker should be marked:

```python
import pytest

pytestmark = pytest.mark.docker_e2e
```

`autoqa` uses this marker to select tests:

- unit: `-m "not docker_e2e"`
- e2e: `-m docker_e2e`

### 3) REST API auth in E2E

E2E tests should use:

- `HA_BASE_URL` (defaults to `http://localhost:8123` outside compose, or `http://homeassistant:8123` inside)
- `HASS_LONG_LIVED_TOKEN` (provided automatically by `autoqa` in E2E mode)

This repo’s Docker E2E tests follow that convention.

---

## Directory layout

- `ha-test-kit/Dockerfile.autoqa`
  - Builds a slim Python image with Home Assistant + pytest tooling.
  - Entrypoint: `python -m ha_test_kit.autoqa`.

- `ha-test-kit/sitecustomize.py`
  - Ensures there is a current asyncio event loop.
  - This helps avoid runtime issues in some environments/tools.

- `ha-test-kit/ha_test_kit/autoqa.py`
  - The harness logic.

- `docker-compose.e2e.yml` (repo root)
  - Brings up two services:
    - `autoqa`: the test runner container.
    - `homeassistant`: HA container.
  - Uses a shared volume for `/ha_config`.

- `run_unit.sh` (repo root)
  - Builds the `autoqa` image and runs **unit mode**.

- `run_e2e.sh` (repo root)
  - Runs Docker Compose E2E stack and exits with the test runner exit code.

---

## Usage

### Unit tests (inside autoqa container)

From repo root:

```bash
./run_unit.sh
```

Optional overrides:

- `AUTOQA_TEST_PATHS` — override which paths are passed to pytest.
- `AUTOQA_PYTEST_ARGS` — extra pytest args.

Examples:

```bash
AUTOQA_TEST_PATHS="custom_components/automation_run_switch/tests" ./run_unit.sh

AUTOQA_PYTEST_ARGS="-k automation_run_switch -vv" ./run_unit.sh
```

### E2E tests (Home Assistant in Docker + autoqa runner)

From repo root:

```bash
./run_e2e.sh
```

Optional overrides:

```bash
HA_VERSION=2026.1.3 ./run_e2e.sh

AUTOQA_TEST_PATHS="tests/test_e2e_dimmer_valve.py" ./run_e2e.sh

AUTOQA_PYTEST_ARGS="-vv --maxfail=1" ./run_e2e.sh
```

---

## Environment variables reference

### Core

- `AUTOQA_MODE`
  - `unit` or `e2e`.

- `AUTOQA_WORK_DIR`
  - Defaults to `/work` (repo root mounted read-only).

- `AUTOQA_TEST_PATHS`
  - If set, overrides test discovery paths (shell-split string).

- `AUTOQA_PYTEST_ARGS`
  - Extra pytest arguments appended to the command.

- `AUTOQA_ENABLE_HA_PYTEST_PLUGIN`
  - If `true`, does not disable HA pytest plugins.
  - Default is `false` (the harness adds `-p no:pytest_homeassistant_custom_component` etc.).

### E2E / Home Assistant

- `HA_BASE_URL`
  - Base URL for HA API.
  - In compose default is `http://homeassistant:8123`.

- `HA_CONFIG_DIR`
  - Defaults to `/ha_config`.

- `AUTOQA_FORCE_SEED`
  - If `true`, overwrites `configuration.yaml` even if it exists in HA config dir.

- `AUTOQA_USERNAME`, `AUTOQA_PASSWORD`
  - Credentials created/used for onboarding/login.

- `AUTOQA_CLIENT_ID`, `AUTOQA_REDIRECT_URI`
  - OAuth-like parameters used by HA auth flow.

- `AUTOQA_TOKEN_LIFESPAN_DAYS`
  - Lifespan for long-lived token creation.

### Exported to tests (E2E)

- `HASS_LONG_LIVED_TOKEN`
  - Long-lived access token created by `autoqa`.

- `RUN_DOCKER_E2E`
  - Set to `1` in E2E mode.

---

## How to add new E2E coverage (recommended workflow)

1) Add or update a HA package YAML file:

- For global E2E config: `tests/ha_packages/<something>.yaml`
- For integration-specific config: `custom_components/<domain>/tests/ha_packages/<domain>.yaml`

2) Add pytest E2E tests:

- Mark them with `pytest.mark.docker_e2e`.
- Use `HA_BASE_URL` and `HASS_LONG_LIVED_TOKEN` for authenticated API calls.

3) Run E2E locally:

```bash
AUTOQA_TEST_PATHS="path/to/your/test_file.py" ./run_e2e.sh
```

---

## Troubleshooting

### "No pytest paths discovered"

`autoqa` expects at least one of these to exist under `/work`:

- `tests/`
- `custom_components/*/tests/`

If your tests live elsewhere, set `AUTOQA_TEST_PATHS`.

### HA starts but tests cannot access API

- Check `HA_BASE_URL` matches the environment.
- In compose, default is `http://homeassistant:8123` for containers.
- For tests running on host, it’s usually `http://localhost:8123`.

### Auth issues

`autoqa` does not seed auth-related `.storage` files on purpose.
If you copy `.storage` manually, you can break onboarding/auth flows.

---

## Notes for agents / automation

- The harness is intentionally **stringly-typed** via env vars; do not hardcode tokens.
- E2E token provisioning is a part of the contract: tests should consume `HASS_LONG_LIVED_TOKEN`.
- Package autodiscovery is the preferred way to configure HA for tests; avoid editing the container’s config in tests.
- Project mount is read-only in both `run_unit.sh` and `docker-compose.e2e.yml` (`/work:ro`).
