from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.middleware.auth import AuthMiddleware


def _make_probe_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(AuthMiddleware)

    @app.get("/probe")
    def probe(request: Request):
        return {
            "user_id": request.state.auth.user_id,
            "authenticated": request.state.auth.authenticated,
        }

    return app


def test_auth_middleware_sets_unauthenticated_context_by_default():
    client = TestClient(_make_probe_app())
    resp = client.get("/probe")
    assert resp.status_code == 200
    assert resp.json() == {"user_id": None, "authenticated": False}


def test_auth_middleware_does_not_block_any_request():
    app = FastAPI()
    app.add_middleware(AuthMiddleware)

    @app.get("/anything")
    def anything():
        return {"ok": True}

    client = TestClient(app)
    resp = client.get("/anything")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
