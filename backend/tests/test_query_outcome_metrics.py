from messengers.base import BaseMessenger


def test_successful_query_outcome_classification() -> None:
    assert BaseMessenger._is_successful_query_outcome(
        result={"status": "success"},
        error=None,
    )
    assert BaseMessenger._is_successful_query_outcome(
        result={"status": "rejected", "reason": "unknown_user"},
        error=None,
    )
    assert BaseMessenger._is_successful_query_outcome(
        result={"status": "error", "error": "insufficient_credits"},
        error=None,
    )


def test_failed_query_outcome_classification() -> None:
    assert not BaseMessenger._is_successful_query_outcome(
        result={"status": "error", "error": "no_organization"},
        error=None,
    )
    assert not BaseMessenger._is_successful_query_outcome(
        result={"status": "timeout_continuing"},
        error=None,
    )
    assert not BaseMessenger._is_successful_query_outcome(
        result={"status": "success"},
        error=RuntimeError("boom"),
    )
