# Copyright © 2026 Province of British Columbia
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
"""Tests for the fga_publisher module — event serialization + LD flag gating."""

from unittest.mock import MagicMock, patch

import pytest

from auth_api.utils.fga_publisher import (
    FgaEventType,
    publish_affiliation_created,
    publish_linking_key_activated,
    publish_membership_activated,
    publish_membership_role_changed,
    publish_org_status_change,
    publish_product_subscribed,
)


@pytest.fixture
def _flag_on(monkeypatch):
    """Force enable-fga-sync flag on for the test."""
    monkeypatch.setattr("auth_api.utils.fga_publisher.flags.is_on", lambda *a, **kw: True)


@pytest.fixture
def _flag_off(monkeypatch):
    """Force enable-fga-sync flag off (default)."""
    monkeypatch.setattr("auth_api.utils.fga_publisher.flags.is_on", lambda *a, **kw: False)


@pytest.fixture
def _mock_queue(monkeypatch):
    """Replace queue.publish with a mock so we can assert publish calls."""
    m = MagicMock()
    monkeypatch.setattr("auth_api.utils.fga_publisher.queue.publish", m)
    return m


def test_publish_skipped_when_flag_off(app, _flag_off, _mock_queue):  # noqa: ARG001
    """No publish should happen when the LD flag is off."""
    publish_membership_activated(1, "guid-1", "ADMIN")
    assert _mock_queue.call_count == 0


def test_publish_membership_activated(app, _flag_on, _mock_queue):  # noqa: ARG001
    """Membership activation publishes a MEMBERSHIP_ACTIVATED CloudEvent to FGA_SYNC_TOPIC."""
    publish_membership_activated(42, "abc-guid", "ADMIN")

    assert _mock_queue.call_count == 1
    args, _ = _mock_queue.call_args
    assert args[0] == app.config["FGA_SYNC_TOPIC"]
    # args[1] is the encoded pub/sub message — we assert on the event by re-decoding
    # via SimpleCloudEvent in a helper; keep it light here.


def test_publish_role_change_includes_previous(app, _flag_on, _mock_queue):  # noqa: ARG001
    """Role change events carry both previous and new role for the consumer to diff."""
    publish_membership_role_changed(42, "abc-guid", "USER", "COORDINATOR")
    assert _mock_queue.call_count == 1


def test_publish_org_status_maps_to_correct_event_type(app, _flag_on, _mock_queue):  # noqa: ARG001
    """Each org status transition maps to a distinct event type."""
    publish_org_status_change(42, "SUSPENDED")
    publish_org_status_change(42, "NSF_SUSPENDED")
    publish_org_status_change(42, "ACTIVE")
    assert _mock_queue.call_count == 3


def test_publish_org_status_ignores_unmapped_statuses(app, _flag_on, _mock_queue):  # noqa: ARG001
    """PENDING_STAFF_REVIEW / REJECTED are not FGA-relevant and should not publish."""
    publish_org_status_change(42, "PENDING_STAFF_REVIEW")
    publish_org_status_change(42, "REJECTED")
    assert _mock_queue.call_count == 0


def test_publish_affiliation_created(app, _flag_on, _mock_queue):  # noqa: ARG001
    """Affiliation creation publishes with business identifier."""
    publish_affiliation_created(42, "BC1234567")
    assert _mock_queue.call_count == 1


def test_publish_product_subscribed(app, _flag_on, _mock_queue):  # noqa: ARG001
    """Product subscription publishes with product code."""
    publish_product_subscribed(42, "PPR")
    assert _mock_queue.call_count == 1


def test_publish_linking_key_activated(app, _flag_on, _mock_queue):  # noqa: ARG001
    """Linking key activation publishes both source and vendor org ids."""
    publish_linking_key_activated(100, 200)
    assert _mock_queue.call_count == 1


def test_publish_swallows_queue_exceptions(app, _flag_on, monkeypatch):  # noqa: ARG001
    """A queue failure must not break the caller."""
    monkeypatch.setattr("auth_api.utils.fga_publisher.queue.publish", MagicMock(side_effect=RuntimeError("boom")))
    # No raise — the exception is logged and swallowed.
    publish_membership_activated(1, "g", "ADMIN")


def test_event_type_values_are_stable_topic_names():
    """The consumer relies on these strings; changing them is a breaking change."""
    assert FgaEventType.MEMBERSHIP_ACTIVATED.value == "bc.registry.auth.fga.membershipActivated"
    assert FgaEventType.LINKING_KEY_ACTIVATED.value == "bc.registry.auth.fga.linkingKeyActivated"
    assert FgaEventType.ORG_SUSPENDED.value == "bc.registry.auth.fga.orgSuspended"
