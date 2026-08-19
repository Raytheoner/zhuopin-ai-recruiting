from fastapi.testclient import TestClient


def test_health_endpoint_reports_logging_state_under_root_path(tmp_path):
    from app.observability.logging_config import setup_logging
    from app.web.server import create_app

    setup_logging(log_dir=str(tmp_path / "logs"), level="INFO", retention_days=30)
    app = create_app(
        db_path=str(tmp_path / "demo.db"),
        gateway_factory=lambda: None,
        root_path="/hr/recruit-agent",
    )
    client = TestClient(app)

    resp = client.get("/hr/recruit-agent/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["logging"]["degraded"] is False
    assert body["logging"]["log_file"].endswith("app.log")
    assert "file" in body["logging"]["handlers"]


def test_health_endpoint_moves_with_the_mount_prefix(tmp_path):
    """部署约束 1：挂到任意子路径都要工作，中间件与新端点都不得硬编码前缀。"""
    from app.observability.logging_config import setup_logging
    from app.web.server import create_app

    setup_logging(log_dir=str(tmp_path / "logs"), level="INFO", retention_days=30)
    app = create_app(
        db_path=str(tmp_path / "demo.db"),
        gateway_factory=lambda: None,
        root_path="/somewhere/else",
    )
    client = TestClient(app)

    assert client.get("/somewhere/else/health").status_code == 200
    assert client.get("/health").status_code == 404
