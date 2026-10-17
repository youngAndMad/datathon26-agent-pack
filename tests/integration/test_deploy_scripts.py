"""Deploy/QA shell scripts: syntax, refusal paths and key handling. No service is started for
the refusal paths (they must fail before spawning anything)."""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = sorted([*(ROOT / "deploy").glob("*.sh"), *(ROOT / "tests" / "offline").glob("*.sh")])


def run(cmd, env=None, timeout=60):
    e = {"PATH": os.environ["PATH"], "HOME": os.environ.get("HOME", "/tmp")}
    e.update(env or {})
    return subprocess.run(cmd, cwd=ROOT, env=e, capture_output=True, text=True, timeout=timeout)


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.name)
def test_bash_syntax(script):
    r = run(["bash", "-n", str(script)])
    assert r.returncode == 0, r.stderr


def test_gen_api_key(tmp_path):
    key = tmp_path / "s" / "jury_api_key"
    r = run(["bash", "deploy/gen_api_key.sh"], {"CASHWATCH_API_KEY_FILE": str(key)})
    assert r.returncode == 0, r.stderr
    value = key.read_text()
    assert len(value) == 64 and all(c in "0123456789abcdef" for c in value)
    assert stat.S_IMODE(key.stat().st_mode) == 0o600
    assert stat.S_IMODE(key.parent.stat().st_mode) == 0o700
    assert value not in r.stdout + r.stderr
    again = run(["bash", "deploy/gen_api_key.sh"], {"CASHWATCH_API_KEY_FILE": str(key)})
    assert again.returncode != 0 and key.read_text() == value  # no silent rotation
    forced = run(["bash", "deploy/gen_api_key.sh", "--force"], {"CASHWATCH_API_KEY_FILE": str(key)})
    assert forced.returncode == 0 and key.read_text() != value


def _start_env(tmp_path, **kw):
    key = tmp_path / "key"
    key.write_text("k" * 64)
    key.chmod(0o600)
    env = {"CASHWATCH_ENV_FILE": str(tmp_path / "none.env"), "CASHWATCH_DATA_DIR": str(tmp_path / "data"),
           "CASHWATCH_API_KEY_FILE": str(key), "CASHWATCH_PORT": "1"}
    env.update(kw)
    return env


def test_start_refuses_fake_in_production(tmp_path):
    r = run(["bash", "deploy/start.sh"], _start_env(tmp_path, CASHWATCH_MODE="production", CASHWATCH_BACKEND="fake"))
    assert r.returncode == 2 and "forbidden in production" in r.stderr
    assert not (tmp_path / "data" / "run").exists()  # nothing was started


def test_start_refuses_unknown_mode(tmp_path):
    r = run(["bash", "deploy/start.sh"], _start_env(tmp_path, CASHWATCH_MODE="demo"))
    assert r.returncode == 2


def test_start_refuses_world_readable_key(tmp_path):
    env = _start_env(tmp_path, CASHWATCH_MODE="test", CASHWATCH_BACKEND="fake")
    Path(env["CASHWATCH_API_KEY_FILE"]).chmod(0o644)
    r = run(["bash", "deploy/start.sh"], env)
    assert r.returncode == 2 and "chmod 600" in r.stderr


def test_start_refuses_inline_secret_env_file(tmp_path):
    envf = tmp_path / "c.env"
    envf.write_text("CASHWATCH_MODE=test\nJURY_API_KEY=abcdef\n")
    env = _start_env(tmp_path)
    env["CASHWATCH_ENV_FILE"] = str(envf)
    r = run(["bash", "deploy/start.sh"], env)
    assert r.returncode == 2 and "inline secret" in r.stderr


def test_env_example_has_no_working_key_and_is_production():
    text = (ROOT / "deploy" / "cashwatch.env.example").read_text()
    assert "CASHWATCH_MODE=production" in text and "CASHWATCH_BACKEND=mlx" in text
    for line in text.splitlines():
        if line.strip().startswith("#") or "=" not in line:
            continue
        name = line.split("=", 1)[0]
        assert not any(s in name for s in ("TOKEN", "SECRET", "PASSWORD")) and not name.endswith("API_KEY")


def test_stop_without_pidfiles_is_clean(tmp_path):
    r = run(["bash", "deploy/stop.sh"], {"CASHWATCH_ENV_FILE": str(tmp_path / "none"),
                                          "CASHWATCH_DATA_DIR": str(tmp_path / "data")})
    assert r.returncode == 0 and "not running" in r.stdout


def test_stop_does_not_signal_foreign_pid(tmp_path):
    run_dir = tmp_path / "data" / "run"
    run_dir.mkdir(parents=True)
    p = subprocess.Popen(["sleep", "30"])
    try:
        (run_dir / "api.pid").write_text(str(p.pid))
        r = run(["bash", "deploy/stop.sh"], {"CASHWATCH_ENV_FILE": str(tmp_path / "none"),
                                              "CASHWATCH_DATA_DIR": str(tmp_path / "data")})
        assert "not a cashwatch process" in r.stderr
        assert p.poll() is None  # untouched
    finally:
        p.kill()


def test_launchd_templates_lint():
    for f in (ROOT / "deploy" / "launchd").glob("*.plist"):
        r = run(["plutil", "-lint", str(f)])
        assert r.returncode == 0, r.stdout
