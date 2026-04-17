from agents.tools import _build_direct_fetch_headers


def test_build_direct_fetch_headers_without_auth() -> None:
    headers = _build_direct_fetch_headers()

    assert headers["User-Agent"] == "Mozilla/5.0 (compatible; Revtops/1.0)"
    assert "Authorization" not in headers
    assert "X-Organization-Id" not in headers


def test_build_direct_fetch_headers_with_auth_and_org() -> None:
    headers = _build_direct_fetch_headers(
        authorization="  Bearer abc123  ",
        x_organization_id="  org_456  ",
    )

    assert headers["Authorization"] == "Bearer abc123"
    assert headers["X-Organization-Id"] == "org_456"
