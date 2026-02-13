from fastapi.testclient import TestClient

from api.main import app


client = TestClient(app)


def test_search_requires_authentication() -> None:
    response = client.get("/api/search", params={"q": "acme"})
    assert response.status_code == 401
