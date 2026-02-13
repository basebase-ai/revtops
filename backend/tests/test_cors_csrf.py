from fastapi.testclient import TestClient

from api.main import app


client = TestClient(app)

WAITLIST_PAYLOAD = {
    "email": "csrf-check@example.com",
    "name": "CSRF Check",
    "title": "Engineer",
    "company_name": "Revtops",
    "num_employees": "1-10",
    "apps_of_interest": ["salesforce"],
    "core_needs": ["insights"],
}


def test_preflight_allows_known_origin() -> None:
    response = client.options(
        "/api/waitlist",
        headers={
            "Origin": "https://app.revtops.com",
            "Access-Control-Request-Method": "POST",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "https://app.revtops.com"


def test_csrf_middleware_blocks_unknown_origin_with_cookie() -> None:
    response = client.post(
        "/api/waitlist",
        headers={"Origin": "https://evil.example", "Cookie": "session=abc"},
        json=WAITLIST_PAYLOAD,
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "CSRF validation failed"


def test_csrf_middleware_allows_non_cookie_requests_from_unknown_origin() -> None:
    response = client.post(
        "/api/does-not-exist",
        headers={"Origin": "https://evil.example"},
        json={"payload": "ok"},
    )

    assert response.status_code == 404
