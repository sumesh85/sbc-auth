# Copyright © 2026 Province of British Columbia
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
"""Publishes tuple-sync events to the fga-sync topic.

Every mutation that changes a user's authorization posture (membership create /
role change / deactivate, affiliation add / remove, product subscribe / unsub,
org status transition, linking-key bind / revoke) emits a CloudEvent here. The
fga-sync queue consumer translates each event into OpenFGA Write / Delete tuple
operations.

Gated by LaunchDarkly flag `enable-fga-sync` so the emission can be turned on
per environment before the consumer is wired.
"""

import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import Enum

from flask import current_app
from simple_cloudevent import SimpleCloudEvent

from auth_api.services.flags import flags
from auth_api.services.gcp_queue import GcpQueue, queue
from auth_api.utils.enums import QueueSources

_SYNC_FLAG = "enable-fga-sync"


class FgaEventType(str, Enum):
    """Event types consumed by the fga-sync queue service."""

    MEMBERSHIP_ACTIVATED = "bc.registry.auth.fga.membershipActivated"
    MEMBERSHIP_ROLE_CHANGED = "bc.registry.auth.fga.membershipRoleChanged"
    MEMBERSHIP_DEACTIVATED = "bc.registry.auth.fga.membershipDeactivated"
    AFFILIATION_CREATED = "bc.registry.auth.fga.affiliationCreated"
    AFFILIATION_REMOVED = "bc.registry.auth.fga.affiliationRemoved"
    PRODUCT_SUBSCRIBED = "bc.registry.auth.fga.productSubscribed"
    PRODUCT_UNSUBSCRIBED = "bc.registry.auth.fga.productUnsubscribed"
    ORG_SUSPENDED = "bc.registry.auth.fga.orgSuspended"
    ORG_NSF_SUSPENDED = "bc.registry.auth.fga.orgNsfSuspended"
    ORG_ACTIVATED = "bc.registry.auth.fga.orgActivated"
    LINKING_KEY_ACTIVATED = "bc.registry.auth.fga.linkingKeyActivated"
    LINKING_KEY_REVOKED = "bc.registry.auth.fga.linkingKeyRevoked"


@dataclass
class FgaSyncEvent:
    """Payload shape shared across all FGA sync events.

    Consumers key off `event_type`; the fields below are a union of what any
    event can carry. Only the fields relevant to the event type will be set.
    """

    event_type: str
    org_id: int | None = None
    user_guid: str | None = None
    role: str | None = None                # ADMIN / COORDINATOR / USER (membership_type_code)
    previous_role: str | None = None       # only for MEMBERSHIP_ROLE_CHANGED
    entity_business_identifier: str | None = None
    product_code: str | None = None
    source_org_id: int | None = None       # linking key: the org that owns the entities
    vendor_org_id: int | None = None       # linking key: the org acting on behalf
    metadata: dict = field(default_factory=dict)


def _publish(event: FgaSyncEvent) -> None:
    """Serialize and publish a single FGA sync event."""
    if not flags.is_on(_SYNC_FLAG, default=False):
        return

    payload = {k: v for k, v in asdict(event).items() if v is not None}
    cloud_event = SimpleCloudEvent(
        id=str(uuid.uuid4()),
        source=QueueSources.AUTH_API.value,
        subject=event.event_type,
        time=datetime.now(tz=UTC).isoformat(),
        type=event.event_type,
        data=payload,
    )
    try:
        queue.publish(
            current_app.config.get("FGA_SYNC_TOPIC"),
            GcpQueue.to_queue_message(cloud_event),
        )
    except Exception as exc:  # pylint: disable=broad-except
        # Sync failures should never break the originating mutation.
        current_app.logger.error("Failed to publish FGA sync event %s: %s", event.event_type, exc)


def publish_membership_activated(org_id: int, user_guid: str, role: str) -> None:
    """User became an ACTIVE member of an org with the given membership role."""
    _publish(
        FgaSyncEvent(
            event_type=FgaEventType.MEMBERSHIP_ACTIVATED.value,
            org_id=org_id,
            user_guid=user_guid,
            role=role,
        )
    )


def publish_membership_role_changed(org_id: int, user_guid: str, previous_role: str, role: str) -> None:
    """Membership role changed (e.g., USER → COORDINATOR)."""
    _publish(
        FgaSyncEvent(
            event_type=FgaEventType.MEMBERSHIP_ROLE_CHANGED.value,
            org_id=org_id,
            user_guid=user_guid,
            previous_role=previous_role,
            role=role,
        )
    )


def publish_membership_deactivated(org_id: int, user_guid: str, role: str) -> None:
    """Membership moved to INACTIVE or REJECTED."""
    _publish(
        FgaSyncEvent(
            event_type=FgaEventType.MEMBERSHIP_DEACTIVATED.value,
            org_id=org_id,
            user_guid=user_guid,
            role=role,
        )
    )


def publish_affiliation_created(org_id: int, entity_business_identifier: str) -> None:
    """Entity was affiliated to an org."""
    _publish(
        FgaSyncEvent(
            event_type=FgaEventType.AFFILIATION_CREATED.value,
            org_id=org_id,
            entity_business_identifier=entity_business_identifier,
        )
    )


def publish_affiliation_removed(org_id: int, entity_business_identifier: str) -> None:
    """Entity was unaffiliated from an org."""
    _publish(
        FgaSyncEvent(
            event_type=FgaEventType.AFFILIATION_REMOVED.value,
            org_id=org_id,
            entity_business_identifier=entity_business_identifier,
        )
    )


def publish_product_subscribed(org_id: int, product_code: str) -> None:
    """ProductSubscription became ACTIVE."""
    _publish(
        FgaSyncEvent(
            event_type=FgaEventType.PRODUCT_SUBSCRIBED.value,
            org_id=org_id,
            product_code=product_code,
        )
    )


def publish_product_unsubscribed(org_id: int, product_code: str) -> None:
    """ProductSubscription became INACTIVE / REJECTED / NOT_SUBSCRIBED."""
    _publish(
        FgaSyncEvent(
            event_type=FgaEventType.PRODUCT_UNSUBSCRIBED.value,
            org_id=org_id,
            product_code=product_code,
        )
    )


def publish_org_status_change(org_id: int, new_status: str) -> None:
    """Org.status_code transitioned. Consumer decides which tuples survive.

    ACTIVE      → re-materialize from SQL (recovery from a prior suspension)
    NSF_SUSPENDED → drop basic_member / coordinator, keep admin
    SUSPENDED   → drop all member tuples
    """
    mapping = {
        "ACTIVE": FgaEventType.ORG_ACTIVATED.value,
        "NSF_SUSPENDED": FgaEventType.ORG_NSF_SUSPENDED.value,
        "SUSPENDED": FgaEventType.ORG_SUSPENDED.value,
    }
    event_type = mapping.get(new_status)
    if not event_type:
        return
    _publish(FgaSyncEvent(event_type=event_type, org_id=org_id, metadata={"new_status": new_status}))


def publish_linking_key_activated(source_org_id: int, vendor_org_id: int) -> None:
    """Linking key was bound and is now ACTIVE — vendor org can act on source org's entities."""
    _publish(
        FgaSyncEvent(
            event_type=FgaEventType.LINKING_KEY_ACTIVATED.value,
            source_org_id=source_org_id,
            vendor_org_id=vendor_org_id,
        )
    )


def publish_linking_key_revoked(source_org_id: int, vendor_org_id: int | None) -> None:
    """Linking key was revoked or expired. vendor_org_id may be None for PENDING keys."""
    _publish(
        FgaSyncEvent(
            event_type=FgaEventType.LINKING_KEY_REVOKED.value,
            source_org_id=source_org_id,
            vendor_org_id=vendor_org_id,
        )
    )
