# Copyright © 2026 Province of British Columbia
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
"""Tests for FgaService — contextual tuple building and Check wrapping.

The underlying OpenFGA client is mocked. These tests assert that:
1. Realm roles from the JWT are correctly translated into `system:global` contextual tuples.
2. Only recognized roles are forwarded; unknown roles are dropped.
3. Failures from the client don't propagate — Check returns False and logs.
"""

from unittest.mock import MagicMock, patch

import pytest

from auth_api.services.fga_service import FgaService
from auth_api.utils.roles import Role


@pytest.fixture(autouse=True)
def _reset_fga_client():
    """Ensure each test gets a fresh memoized client."""
    FgaService.reset()
    yield
    FgaService.reset()


def test_no_contextual_tuples_when_jwt_roles_empty():
    """No realm roles → no contextual tuples appended to the Check request."""
    tuples = FgaService._build_realm_role_tuples("guid-1", [])  # noqa: SLF001
    assert tuples == []


def test_staff_role_becomes_system_global_staff_tuple():
    """The STAFF realm role → user:{guid} staff@system:global tuple."""
    tuples = FgaService._build_realm_role_tuples("guid-1", [Role.STAFF.value])  # noqa: SLF001
    assert len(tuples) == 1
    t = tuples[0]
    assert t.user == "user:guid-1"
    assert t.relation == "staff"
    assert t.object == "system:global"


def test_multiple_roles_each_produce_a_tuple():
    """STAFF + SYSTEM together produce two contextual tuples with distinct relations."""
    tuples = FgaService._build_realm_role_tuples(  # noqa: SLF001
        "guid-1", [Role.STAFF.value, Role.SYSTEM.value]
    )
    relations = sorted(t.relation for t in tuples)
    assert relations == ["staff", "system_account"]


def test_external_staff_readonly_maps_to_its_own_relation():
    """EXTERNAL_STAFF_READONLY has its own dedicated relation in the FGA model."""
    tuples = FgaService._build_realm_role_tuples(  # noqa: SLF001
        "guid-1", [Role.EXTERNAL_STAFF_READONLY.value]
    )
    assert len(tuples) == 1
    assert tuples[0].relation == "external_staff_ro"


def test_unknown_role_is_dropped():
    """A role not in the mapping (e.g. VIEWER) doesn't produce a contextual tuple."""
    tuples = FgaService._build_realm_role_tuples(  # noqa: SLF001
        "guid-1", [Role.VIEWER.value, "some-unmapped-role"]
    )
    assert tuples == []


def test_check_forwards_contextual_tuples(app):  # noqa: ARG001
    """FgaService.check builds a ClientCheckRequest with the expected contextual tuples."""
    mock_client = MagicMock()
    mock_client.check.return_value = MagicMock(allowed=True)

    with patch.object(FgaService, "_get_client", return_value=mock_client):
        result = FgaService.check(
            user_guid="guid-1",
            relation="filer",
            object_type="entity",
            object_id="BC1234567",
            jwt_roles=[Role.STAFF.value],
        )

    assert result is True
    assert mock_client.check.call_count == 1
    call_arg = mock_client.check.call_args[0][0]
    assert call_arg.user == "user:guid-1"
    assert call_arg.relation == "filer"
    assert call_arg.object == "entity:BC1234567"
    # One contextual tuple for STAFF
    assert len(call_arg.contextual_tuples) == 1
    assert call_arg.contextual_tuples[0].relation == "staff"


def test_check_returns_false_when_client_raises(app):  # noqa: ARG001
    """A client failure must not propagate — Check returns False and logs."""
    mock_client = MagicMock()
    mock_client.check.side_effect = RuntimeError("openfga is down")

    with patch.object(FgaService, "_get_client", return_value=mock_client):
        result = FgaService.check("guid-1", "filer", "entity", "BC1234567", [])

    assert result is False


def test_list_objects_strips_type_prefix(app):  # noqa: ARG001
    """list_objects returns bare IDs, not type-prefixed refs."""
    mock_client = MagicMock()
    mock_client.list_objects.return_value = MagicMock(objects=["entity:BC1", "entity:BC2"])

    with patch.object(FgaService, "_get_client", return_value=mock_client):
        result = FgaService.list_objects("guid-1", "viewer", "entity", [])

    assert result == ["BC1", "BC2"]
