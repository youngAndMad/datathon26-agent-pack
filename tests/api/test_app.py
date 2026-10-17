"""App wiring: security headers/no CORS, static SPA, sweeper lifespan, production refusal."""

from __future__ import annotations

import pytest
from jobfakes import enqueue, make_settings

from cashwatch.api import create_app
from cashwatch.api.main import run
from cashwatch.deadline import utc_now
from cashwatch.storage import create_store


def test_security_headers_and_no_cors(env):
    r = env.client.get("/healthz", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in r.headers
    assert "default-src 'self'" in r.headers["content-security-policy"]
    assert "media-src 'self' blob:" in r.headers["content-security-policy"]
    assert r.headers["x-content-type-options"] == "nosniff"
    r = env.client.options("/api/v1/analyze", headers={"Origin": "https://evil.example",
                                                       "Access-Control-Request-Method": "POST"})
    assert r.status_code == 401 and "access-control-allow-origin" not in r.headers
    assert env.client.get("/docs").status_code == 404 and env.client.get("/openapi.json").status_code == 404


def test_static_spa_served_same_origin(tmp_path):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>ui</title>")
    (dist / "assets" / "app.js").write_text("console.log(1)")
    s = make_settings(tmp_path, frontend_dist=dist)
    from starlette.testclient import TestClient

    c = TestClient(create_app(s, sweep_interval_sec=0))
    assert c.get("/").text.startswith("<!doctype html>")
    assert c.get("/assets/app.js").text == "console.log(1)"
    assert c.get("/jobs/some/deep/route").text.startswith("<!doctype html>")  # SPA fallback
    assert c.get("/../../etc/passwd").text.startswith("<!doctype html>")
    assert c.get("/api/v1/nothing").status_code == 401
    key = s.read_api_key()
    r = c.get("/api/v1/nothing", headers={"X-API-Key": key})
    assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"


def test_startup_sweeper_resolves_expired_jobs(tmp_path):
    from datetime import timedelta

    from starlette.testclient import TestClient

    s = make_settings(tmp_path)
    st = create_store(s)
    jid = enqueue(st, started=utc_now() - timedelta(seconds=300))
    with TestClient(create_app(s, st, sweep_interval_sec=0)):
        pass
    v = st.get_job(jid)
    assert v.state == "failed" and v.error_code == "deadline_exceeded"


def test_production_refuses_mock_backend(tmp_path, monkeypatch, capsys):
    s = make_settings(tmp_path)
    monkeypatch.setenv("CASHWATCH_MODE", "production")
    monkeypatch.setenv("CASHWATCH_BACKEND", "fake")
    monkeypatch.setenv("CASHWATCH_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("CASHWATCH_API_KEY_FILE", str(s.api_key_file))
    with pytest.raises(SystemExit) as e:
        run()
    assert e.value.code == 2
    err = capsys.readouterr().err
    assert "fake/mock" in err and s.read_api_key() not in err


def test_weak_or_missing_key_refuses(tmp_path, monkeypatch, capsys):
    weak = tmp_path / "weak"
    weak.write_text("short")
    monkeypatch.setenv("CASHWATCH_MODE", "test")
    monkeypatch.setenv("CASHWATCH_BACKEND", "fake")
    monkeypatch.setenv("CASHWATCH_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("CASHWATCH_API_KEY_FILE", str(weak))
    with pytest.raises(SystemExit):
        run()
    assert "short" not in capsys.readouterr().err
