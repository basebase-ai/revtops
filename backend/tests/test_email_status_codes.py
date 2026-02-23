from services.email import _resend_request_succeeded


def test_resend_success_accepts_all_2xx() -> None:
    assert _resend_request_succeeded(200)
    assert _resend_request_succeeded(202)
    assert _resend_request_succeeded(204)


def test_resend_success_rejects_non_2xx() -> None:
    assert not _resend_request_succeeded(199)
    assert not _resend_request_succeeded(400)
    assert not _resend_request_succeeded(500)
