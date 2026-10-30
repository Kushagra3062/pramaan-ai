from fastapi.testclient import TestClient

from app.main import app


def test_liveness_is_available_without_database() -> None:
    client = TestClient(app)
    response = client.get("/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "check": "live"}
    assert response.headers.get("x-request-id")


def test_internal_model_endpoint_uses_stub_in_tests(monkeypatch) -> None:
    # Never spend provider credits as a side effect of running the test suite.
    from app.api.routes import dev

    monkeypatch.setattr(
        dev,
        "smoke_test_model",
        lambda: {"status": "ok", "model": "test-model", "output": "READY", "duration_ms": 1, "usage": {}},
    )
    client = TestClient(app)
    response = client.post("/internal/dev/meta-smoke")
    assert response.status_code == 200
    assert response.json()["output"] == "READY"
