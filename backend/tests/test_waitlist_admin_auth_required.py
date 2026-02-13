from fastapi.testclient import TestClient

from api.main import app


client = TestClient(app)


def test_waitlist_admin_list_requires_authentication() -> None:
    response = client.get("/api/waitlist/admin")
    assert response.status_code == 401


def test_waitlist_admin_invite_requires_authentication() -> None:
    response = client.post("/api/waitlist/admin/00000000-0000-0000-0000-000000000000/invite")
    assert response.status_code == 401
