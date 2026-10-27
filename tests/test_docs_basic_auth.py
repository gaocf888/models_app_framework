"""OpenAPI /docs Basic 鉴权（不启动完整 create_app，避免 RAG/ES 依赖）。"""

from __future__ import annotations

import base64

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.auth.docs_basic import register_protected_docs


def _auth_header(user: str, password: str) -> dict[str, str]:
    token = base64.b64encode(f"{user}:{password}".encode("utf-8")).decode("ascii")
    return {"Authorization": f"Basic {token}"}


def _app() -> FastAPI:
    app = FastAPI(title="Docs Auth Test", docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/ping")
    def ping() -> dict[str, str]:
        return {"ok": "1"}

    register_protected_docs(app)
    return app


def test_docs_requires_basic_when_enabled(monkeypatch) -> None:
    monkeypatch.setenv("DOCS_BASIC_AUTH_ENABLED", "true")
    monkeypatch.setenv("DOCS_USERNAME", "docs_admin")
    monkeypatch.setenv("DOCS_PASSWORD", "s3cret-docs")
    client = TestClient(_app())

    assert client.get("/ping").status_code == 200
    for path in ("/docs", "/redoc", "/openapi.json"):
        resp = client.get(path)
        assert resp.status_code == 401
        assert resp.headers.get("WWW-Authenticate", "").lower().startswith("basic")

    bad = client.get("/openapi.json", headers=_auth_header("docs_admin", "wrong"))
    assert bad.status_code == 401

    ok = client.get("/openapi.json", headers=_auth_header("docs_admin", "s3cret-docs"))
    assert ok.status_code == 200
    assert "openapi" in ok.json()

    docs = client.get("/docs", headers=_auth_header("docs_admin", "s3cret-docs"))
    assert docs.status_code == 200
    assert "text/html" in docs.headers.get("content-type", "")


def test_docs_open_when_basic_disabled(monkeypatch) -> None:
    monkeypatch.setenv("DOCS_BASIC_AUTH_ENABLED", "false")
    client = TestClient(_app())
    assert client.get("/openapi.json").status_code == 200
    assert client.get("/docs").status_code == 200


def test_docs_401_when_credentials_missing(monkeypatch) -> None:
    monkeypatch.setenv("DOCS_BASIC_AUTH_ENABLED", "true")
    monkeypatch.delenv("DOCS_USERNAME", raising=False)
    monkeypatch.delenv("DOCS_USER", raising=False)
    monkeypatch.delenv("DOCS_PASSWORD", raising=False)
    monkeypatch.delenv("DOCS_PASS", raising=False)
    client = TestClient(_app())
    assert (
        client.get(
            "/docs",
            headers=_auth_header("anyone", "anything"),
        ).status_code
        == 401
    )
