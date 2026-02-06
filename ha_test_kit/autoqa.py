from __future__ import annotations

import asyncio
import json
import os
import shutil
import shlex
import subprocess
import sys
import time
from pathlib import Path

import requests
import websockets

# ---------------------------------------------------------------------------
# Minimal HA configuration.yaml generated when the project does not provide
# its own.  The ``packages: !include_dir_named packages`` directive enables
# autodiscovery of per-component config fragments.
# ---------------------------------------------------------------------------
_MINIMAL_HA_CONFIG = """\
homeassistant:
  name: AutoQA Test
  packages: !include_dir_named packages

http:
  server_host: 0.0.0.0
  trusted_proxies:
    - 127.0.0.1
    - "::1"
    - 172.16.0.0/12
    - 192.168.0.0/16
  use_x_forwarded_for: false

logger:
  default: info
"""


def _log(message: str) -> None:
    print(f"[autoqa] {message}", flush=True)


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def _normalize_base_url(url: str) -> str:
    url = url.strip()
    if url.endswith("/"):
        url = url[:-1]
    return url


def _wait_for_ha_ready(base_url: str, timeout_s: int = 180) -> None:
    base_url = _normalize_base_url(base_url)
    deadline = time.time() + timeout_s
    last_error: str | None = None

    while time.time() < deadline:
        try:
            resp = requests.get(f"{base_url}/api/", timeout=5)
            if resp.status_code in (200, 401):
                _log(f"HA API ready (status {resp.status_code})")
                return
            last_error = f"Unexpected status: {resp.status_code}"
        except requests.RequestException as exc:
            last_error = str(exc)

        time.sleep(2)

    raise RuntimeError(f"Timed out waiting for HA API: {last_error}")


# ---------------------------------------------------------------------------
# HA config seeding & package autodiscovery
# ---------------------------------------------------------------------------

def _discover_ha_packages(work_dir: Path) -> list[Path]:
    """Find HA package YAML files from well-known autodiscovery paths.

    Searched locations (relative to *work_dir*):
      - tests/ha_packages/*.yaml
      - custom_components/*/tests/ha_packages/*.yaml
    """
    found: list[Path] = []

    project_packages = work_dir / "tests" / "ha_packages"
    if project_packages.is_dir():
        found.extend(sorted(project_packages.glob("*.yaml")))

    cc_dir = work_dir / "custom_components"
    if cc_dir.is_dir():
        for domain_dir in sorted(cc_dir.iterdir()):
            pkg_dir = domain_dir / "tests" / "ha_packages"
            if pkg_dir.is_dir():
                found.extend(sorted(pkg_dir.glob("*.yaml")))

    return found


def _seed_ha_configuration(ha_config_dir: Path, work_dir: Path) -> None:
    """Seed HA configuration directory for e2e runs.

    1. Copy ``configuration.yaml`` from the project or generate a minimal one.
    2. Autodiscover HA package files and place them into ``packages/``.
    3. Copy ``.storage`` registry files from the project (if present).
    """
    ha_config_dir.mkdir(parents=True, exist_ok=True)

    dst_cfg = ha_config_dir / "configuration.yaml"
    force = _env_bool("AUTOQA_FORCE_SEED", default=False)

    if not dst_cfg.exists() or force:
        src_cfg = work_dir / "tests" / "ha_config" / "configuration.yaml"
        if not src_cfg.exists():
            # Fallback: search in per-component test dirs
            cc_dir = work_dir / "custom_components"
            if cc_dir.is_dir():
                for domain_dir in sorted(cc_dir.iterdir()):
                    candidate = domain_dir / "tests" / "ha_config" / "configuration.yaml"
                    if candidate.exists():
                        src_cfg = candidate
                        break

        if src_cfg.exists():
            shutil.copyfile(src_cfg, dst_cfg)
            _log(f"Seeded configuration.yaml from {src_cfg}")
        else:
            dst_cfg.write_text(_MINIMAL_HA_CONFIG, encoding="utf-8")
            _log("Generated minimal configuration.yaml (no project config found)")
    else:
        _log("configuration.yaml already exists; not overwriting")

    # --- Autodiscover & collect HA packages --------------------------------
    packages_dir = ha_config_dir / "packages"
    packages_dir.mkdir(parents=True, exist_ok=True)

    discovered = _discover_ha_packages(work_dir)
    for pkg_file in discovered:
        dst = packages_dir / pkg_file.name
        shutil.copyfile(pkg_file, dst)
        _log(f"Collected HA package: {pkg_file.name} (from {pkg_file})")

    if not discovered:
        _log("No HA packages discovered (checked tests/ha_packages/ and custom_components/*/tests/ha_packages/)")

    # --- Copy .storage registries ------------------------------------------
    # Files that must NOT be seeded — they interfere with onboarding / auth.
    _STORAGE_SKIP = {
        "auth",
        "auth_provider.homeassistant",
        "onboarding",
        "person",
        "http.auth",
    }

    # Autodiscover .storage dirs from multiple locations
    storage_dirs: list[Path] = []
    legacy_storage = work_dir / "tests" / "ha_config" / ".storage"
    if legacy_storage.is_dir():
        storage_dirs.append(legacy_storage)

    cc_dir = work_dir / "custom_components"
    if cc_dir.is_dir():
        for domain_dir in sorted(cc_dir.iterdir()):
            candidate = domain_dir / "tests" / "ha_config" / ".storage"
            if candidate.is_dir():
                storage_dirs.append(candidate)

    if not storage_dirs:
        return

    dst_storage_dir = ha_config_dir / ".storage"
    dst_storage_dir.mkdir(parents=True, exist_ok=True)

    for src_storage_dir in storage_dirs:
        for src_file in sorted(src_storage_dir.iterdir()):
            if not src_file.is_file():
                continue
            if src_file.name in _STORAGE_SKIP:
                _log(f"Skipped seeding .storage/{src_file.name} (auth-related)")
                continue
            try:
                shutil.copyfile(src_file, dst_storage_dir / src_file.name)
            except PermissionError:
                _log(f"Skipped seeding {src_file.name} (permission denied)")
                continue
            _log(f"Seeded .storage/{src_file.name} (from {src_storage_dir})")


def _touch_ready_flag(ha_config_dir: Path) -> None:
    ready_flag = ha_config_dir / ".autoqa_ready"
    ready_flag.parent.mkdir(parents=True, exist_ok=True)
    ready_flag.write_text("ready\n", encoding="utf-8")


def _create_onboarding_user(
    base_url: str,
    *,
    username: str,
    password: str,
    client_id: str,
    name: str = "AutoQA",
    language: str = "en",
) -> str | None:
    base_url = _normalize_base_url(base_url)

    payload = {
        "name": name,
        "username": username,
        "password": password,
        "client_id": client_id,
        "language": language,
    }

    resp = requests.post(
        f"{base_url}/api/onboarding/users",
        json=payload,
        timeout=30,
    )

    if resp.status_code == 200:
        data = resp.json()
        auth_code = data.get("auth_code")
        if not auth_code:
            raise RuntimeError(f"Onboarding succeeded but auth_code missing: {data}")
        _log("Created onboarding user")
        return str(auth_code)

    if resp.status_code in (403, 404):
        _log(f"Onboarding unavailable (status {resp.status_code}); will use login_flow fallback")
        return None

    raise RuntimeError(
        f"Failed to create onboarding user: status={resp.status_code} body={resp.text}"
    )


def _login_flow_auth_code(
    base_url: str,
    *,
    username: str,
    password: str,
    client_id: str,
    redirect_uri: str,
) -> str:
    base_url = _normalize_base_url(base_url)

    providers_resp = requests.get(f"{base_url}/auth/providers", timeout=30)
    providers_resp.raise_for_status()
    providers_payload = providers_resp.json()

    providers = providers_payload.get("providers")
    if not isinstance(providers, list) or not providers:
        raise RuntimeError(f"No auth providers returned: {providers_payload}")

    handler = None
    for prv in providers:
        if prv.get("type") == "homeassistant":
            handler = [prv.get("type"), prv.get("id")]
            break

    if handler is None:
        prv = providers[0]
        handler = [prv.get("type"), prv.get("id")]

    init_payload = {
        "client_id": client_id,
        "handler": handler,
        "redirect_uri": redirect_uri,
        "type": "authorize",
    }

    init_resp = requests.post(f"{base_url}/auth/login_flow", json=init_payload, timeout=30)
    init_resp.raise_for_status()
    init_flow = init_resp.json()

    flow_id = init_flow.get("flow_id")
    if not flow_id:
        raise RuntimeError(f"login_flow init missing flow_id: {init_flow}")

    schema = init_flow.get("data_schema")
    field_names: set[str] = set()
    if isinstance(schema, list):
        for item in schema:
            if isinstance(item, dict) and "name" in item:
                field_names.add(str(item["name"]))

    user_input: dict[str, object] = {"client_id": client_id}
    if "username" in field_names or not field_names:
        user_input["username"] = username
    if "password" in field_names or not field_names:
        user_input["password"] = password
    if "remember_me" in field_names:
        user_input["remember_me"] = True

    step_resp = requests.post(
        f"{base_url}/auth/login_flow/{flow_id}",
        json=user_input,
        timeout=30,
    )
    step_resp.raise_for_status()
    step = step_resp.json()

    if step.get("type") != "create_entry":
        raise RuntimeError(f"Unexpected login_flow step type: {step}")

    auth_code = step.get("result")
    if not auth_code:
        raise RuntimeError(f"login_flow finished but result missing: {step}")

    _log("login_flow succeeded")
    return str(auth_code)


def _exchange_auth_code_for_access_token(
    base_url: str, *, client_id: str, auth_code: str
) -> str:
    base_url = _normalize_base_url(base_url)

    resp = requests.post(
        f"{base_url}/auth/token",
        data={
            "grant_type": "authorization_code",
            "code": auth_code,
            "client_id": client_id,
        },
        timeout=30,
    )
    resp.raise_for_status()

    payload = resp.json()
    access_token = payload.get("access_token")
    if not access_token:
        raise RuntimeError(f"No access_token in /auth/token response: {payload}")

    return str(access_token)


async def _ws_create_long_lived_token(
    base_url: str,
    *,
    access_token: str,
    client_name: str = "autoqa",
    lifespan_days: int = 365,
) -> str:
    base_url = _normalize_base_url(base_url)
    if base_url.startswith("https://"):
        ws_url = "wss://" + base_url.removeprefix("https://")
    elif base_url.startswith("http://"):
        ws_url = "ws://" + base_url.removeprefix("http://")
    else:
        raise RuntimeError(f"Unsupported HA_BASE_URL scheme: {base_url}")

    ws_url = f"{ws_url}/api/websocket"

    async with websockets.connect(ws_url, ping_interval=None) as ws:
        greeting = json.loads(await ws.recv())
        if greeting.get("type") != "auth_required":
            raise RuntimeError(f"Unexpected websocket greeting: {greeting}")

        await ws.send(json.dumps({"type": "auth", "access_token": access_token}))
        auth_result = json.loads(await ws.recv())
        if auth_result.get("type") != "auth_ok":
            raise RuntimeError(f"Websocket auth failed: {auth_result}")

        req_id = 1
        await ws.send(
            json.dumps(
                {
                    "id": req_id,
                    "type": "auth/long_lived_access_token",
                    "client_name": client_name,
                    "lifespan": lifespan_days,
                }
            )
        )
        token_result = json.loads(await ws.recv())
        if not token_result.get("success"):
            raise RuntimeError(f"LLAT creation failed: {token_result}")

        token = token_result.get("result")
        if not token:
            raise RuntimeError(f"LLAT result missing: {token_result}")

        return str(token)


def _discover_pytest_paths(work_dir: Path) -> list[str]:
    if (override := os.environ.get("AUTOQA_TEST_PATHS")):
        return shlex.split(override)

    paths: list[Path] = []
    if (work_dir / "tests").exists():
        paths.append(work_dir / "tests")

    cc_dir = work_dir / "custom_components"
    if cc_dir.is_dir():
        for domain_dir in sorted(cc_dir.iterdir()):
            tests_dir = domain_dir / "tests"
            if tests_dir.is_dir():
                paths.append(tests_dir)

    return [str(p) for p in paths]


def _run_pytest(*, mode: str, extra_env: dict[str, str]) -> int:
    args = [
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "-o",
        "cache_dir=/tmp/pytest_cache",
        "-o",
        "asyncio_mode=auto",
        "--basetemp=/tmp/pytest_basetemp",
    ]

    if mode == "unit":
        args += ["-m", "not docker_e2e"]
    elif mode == "e2e":
        args += ["-m", "docker_e2e"]
    else:
        raise RuntimeError(f"Unknown AUTOQA_MODE: {mode}")

    enable_ha_pytest_plugin = _env_bool("AUTOQA_ENABLE_HA_PYTEST_PLUGIN", default=False)
    if not enable_ha_pytest_plugin:
        args += [
            "-p",
            "no:pytest_homeassistant_custom_component",
            "-p",
            "no:homeassistant-custom-component",
        ]

    if (extra_args := os.environ.get("AUTOQA_PYTEST_ARGS")):
        args += shlex.split(extra_args)

    work_dir = Path(os.environ.get("AUTOQA_WORK_DIR", "/work"))
    paths = _discover_pytest_paths(work_dir)
    if not paths:
        raise RuntimeError(f"No pytest paths discovered under {work_dir}")

    args += paths

    env = dict(os.environ)
    env.update(extra_env)

    _log("Running: " + " ".join(args))
    proc = subprocess.run(args, env=env)
    return int(proc.returncode)


def main() -> int:
    mode = os.environ.get("AUTOQA_MODE", "unit").strip().lower()

    work_dir = Path(os.environ.get("AUTOQA_WORK_DIR", "/work"))

    if mode == "e2e":
        ha_config_dir = Path(os.environ.get("HA_CONFIG_DIR", "/ha_config"))
        _seed_ha_configuration(ha_config_dir, work_dir)
        _touch_ready_flag(ha_config_dir)

        base_url = os.environ.get("HA_BASE_URL", "http://homeassistant:8123")
        base_url = _normalize_base_url(base_url)

        _wait_for_ha_ready(base_url)

        autoqa_username = os.environ.get("AUTOQA_USERNAME", "autoqa")
        autoqa_password = os.environ.get("AUTOQA_PASSWORD", "autoqa")

        client_id = os.environ.get("AUTOQA_CLIENT_ID", f"{base_url}/")
        redirect_uri = os.environ.get("AUTOQA_REDIRECT_URI", f"{base_url}/")

        auth_code = _create_onboarding_user(
            base_url,
            username=autoqa_username,
            password=autoqa_password,
            client_id=client_id,
        )

        if auth_code is None:
            auth_code = _login_flow_auth_code(
                base_url,
                username=autoqa_username,
                password=autoqa_password,
                client_id=client_id,
                redirect_uri=redirect_uri,
            )

        access_token = _exchange_auth_code_for_access_token(
            base_url,
            client_id=client_id,
            auth_code=auth_code,
        )

        lifespan_days = int(os.environ.get("AUTOQA_TOKEN_LIFESPAN_DAYS", "365"))
        llat = asyncio.run(
            _ws_create_long_lived_token(
                base_url,
                access_token=access_token,
                lifespan_days=lifespan_days,
            )
        )

        extra_env = {
            "HASS_LONG_LIVED_TOKEN": llat,
            "HA_BASE_URL": base_url,
            "RUN_DOCKER_E2E": os.environ.get("RUN_DOCKER_E2E", "1"),
            "HA_CONFIG_DIR": str(ha_config_dir),
        }

        return _run_pytest(mode=mode, extra_env=extra_env)

    if mode == "unit":
        extra_env = {
            "RUN_DOCKER_E2E": os.environ.get("RUN_DOCKER_E2E", ""),
        }
        return _run_pytest(mode=mode, extra_env=extra_env)

    raise RuntimeError(f"Unknown AUTOQA_MODE: {mode}")


if __name__ == "__main__":
    raise SystemExit(main())
