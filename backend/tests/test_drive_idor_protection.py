from __future__ import annotations

from uuid import uuid4

from fastapi.testclient import TestClient

from api.auth_middleware import AuthContext, get_current_auth
from api.main import app
from api.routes import drive


client = TestClient(app)


def test_drive_search_requires_authentication() -> None:
    response = client.get("/api/drive/search", params={"q": "pipeline"})
    assert response.status_code == 401


def test_drive_search_uses_auth_context_not_query_params(monkeypatch) -> None:
    auth_user_id = uuid4()
    auth_org_id = uuid4()
    foreign_user_id = uuid4()
    foreign_org_id = uuid4()

    app.dependency_overrides[get_current_auth] = lambda: AuthContext(
        user_id=auth_user_id,
        organization_id=auth_org_id,
        email="owner@example.com",
        role="user",
        is_global_admin=False,
    )

    captured: dict[str, str] = {}

    class FakeDriveConnector:
        def __init__(self, organization_id: str, user_id: str) -> None:
            captured["organization_id"] = organization_id
            captured["user_id"] = user_id

        async def search_files(self, q: str, limit: int = 20):
            return []

    monkeypatch.setattr(drive, "GoogleDriveConnector", FakeDriveConnector)

    try:
        response = client.get(
            "/api/drive/search",
            params={
                "q": "pipeline",
                "user_id": str(foreign_user_id),
                "organization_id": str(foreign_org_id),
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert captured["organization_id"] == str(auth_org_id)
    assert captured["user_id"] == str(auth_user_id)
