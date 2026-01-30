import logging

from fastapi.testclient import TestClient

from api.main import app


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


client = TestClient(app)


def test_root_health_check() -> None:
    response = client.get("/")
    logger.info(
        "Root health check response",
        extra={"status_code": response.status_code, "body": response.json()},
    )
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_health_check() -> None:
    response = client.get("/health")
    logger.info(
        "Health check response",
        extra={"status_code": response.status_code, "body": response.json()},
    )
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_openapi_schema_available() -> None:
    response = client.get("/openapi.json")
    logger.info("OpenAPI response", extra={"status_code": response.status_code})
    assert response.status_code == 200
    payload = response.json()
    assert payload["info"]["title"] == "Revenue Copilot API"
    assert "paths" in payload
