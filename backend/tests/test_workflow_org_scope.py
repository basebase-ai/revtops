from uuid import uuid4

import pytest
from fastapi import HTTPException

from api.auth_middleware import AuthContext
from api.routes.workflows import _enforce_workflow_org_scope


def _auth_context(*, org_id, is_global_admin: bool = False) -> AuthContext:
    return AuthContext(
        user_id=uuid4(),
        organization_id=org_id,
        email="user@example.com",
        role="global_admin" if is_global_admin else "user",
        is_global_admin=is_global_admin,
    )


def test_enforce_workflow_org_scope_allows_matching_org() -> None:
    org_id = uuid4()
    auth = _auth_context(org_id=org_id)

    resolved = _enforce_workflow_org_scope(str(org_id), auth)

    assert resolved == org_id


def test_enforce_workflow_org_scope_blocks_cross_org_access() -> None:
    auth = _auth_context(org_id=uuid4())

    with pytest.raises(HTTPException) as exc_info:
        _enforce_workflow_org_scope(str(uuid4()), auth)

    assert exc_info.value.status_code == 403


def test_enforce_workflow_org_scope_allows_global_admin_cross_org() -> None:
    auth = _auth_context(org_id=uuid4(), is_global_admin=True)
    requested = uuid4()

    resolved = _enforce_workflow_org_scope(str(requested), auth)

    assert resolved == requested
