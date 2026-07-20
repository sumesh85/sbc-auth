# Copyright © 2026 Province of British Columbia
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
"""Translate FGA sync events into OpenFGA Write / Delete tuple operations."""

from openfga_sdk.client.models import ClientTuple, ClientWriteRequest

from auth_api.utils.fga_publisher import FgaEventType
from auth_api.utils.roles import ADMIN, COORDINATOR, USER
from flask import current_app

_ROLE_RELATION = {
    ADMIN: "admin",
    COORDINATOR: "coordinator",
    USER: "basic_member",
}


def _org_membership_tuple(org_id: int, user_guid: str, role: str) -> ClientTuple | None:
    relation = _ROLE_RELATION.get(role)
    if not relation:
        return None
    return ClientTuple(user=f"user:{user_guid}", relation=relation, object=f"org:{org_id}")


def _affiliation_tuple(org_id: int, business_identifier: str) -> ClientTuple:
    return ClientTuple(user=f"org:{org_id}", relation="affiliated_org", object=f"entity:{business_identifier}")


def _product_tuple(org_id: int, product_code: str) -> ClientTuple:
    return ClientTuple(user=f"org:{org_id}", relation="subscribed_org", object=f"product:{product_code}")


def _linking_key_tuple(source_org_id: int, vendor_org_id: int) -> ClientTuple:
    return ClientTuple(
        user=f"org:{vendor_org_id}#member",
        relation="delegate",
        object=f"org:{source_org_id}",
    )


def apply_event(client, event_type: str, data: dict) -> None:
    """Dispatch a single event to the right OpenFGA operation.

    Kept as a pure function of (client, event_type, data) so it's trivially unit-testable
    with a mocked client. Unknown event types are logged and dropped — the queue message
    is still ack'd so a bad event doesn't wedge the subscription.
    """
    match event_type:
        case FgaEventType.MEMBERSHIP_ACTIVATED.value:
            if t := _org_membership_tuple(data["org_id"], data["user_guid"], data["role"]):
                client.write(ClientWriteRequest(writes=[t]))

        case FgaEventType.MEMBERSHIP_ROLE_CHANGED.value:
            old = _org_membership_tuple(data["org_id"], data["user_guid"], data["previous_role"])
            new = _org_membership_tuple(data["org_id"], data["user_guid"], data["role"])
            if old:
                client.write(ClientWriteRequest(deletes=[old]))
            if new:
                client.write(ClientWriteRequest(writes=[new]))

        case FgaEventType.MEMBERSHIP_DEACTIVATED.value:
            if t := _org_membership_tuple(data["org_id"], data["user_guid"], data["role"]):
                client.write(ClientWriteRequest(deletes=[t]))

        case FgaEventType.AFFILIATION_CREATED.value:
            client.write(
                ClientWriteRequest(writes=[_affiliation_tuple(data["org_id"], data["entity_business_identifier"])])
            )
        case FgaEventType.AFFILIATION_REMOVED.value:
            client.write(
                ClientWriteRequest(deletes=[_affiliation_tuple(data["org_id"], data["entity_business_identifier"])])
            )

        case FgaEventType.PRODUCT_SUBSCRIBED.value:
            client.write(ClientWriteRequest(writes=[_product_tuple(data["org_id"], data["product_code"])]))
        case FgaEventType.PRODUCT_UNSUBSCRIBED.value:
            client.write(ClientWriteRequest(deletes=[_product_tuple(data["org_id"], data["product_code"])]))

        case FgaEventType.LINKING_KEY_ACTIVATED.value:
            client.write(
                ClientWriteRequest(writes=[_linking_key_tuple(data["source_org_id"], data["vendor_org_id"])])
            )
        case FgaEventType.LINKING_KEY_REVOKED.value:
            if data.get("vendor_org_id"):
                client.write(
                    ClientWriteRequest(deletes=[_linking_key_tuple(data["source_org_id"], data["vendor_org_id"])])
                )

        case FgaEventType.ORG_SUSPENDED.value | FgaEventType.ORG_NSF_SUSPENDED.value | FgaEventType.ORG_ACTIVATED.value:
            # Handled by a materializer that re-reads DB state for the affected org.
            # See auth_api.scripts.fga_backfill --org-id <org_id>.
            _rematerialize_org(client, data["org_id"], event_type)

        case _:
            current_app.logger.warning("fga-sync: unknown event type %s", event_type)


def _rematerialize_org(client, org_id: int, event_type: str) -> None:
    """Rebuild all tuples for a single org from SQL. Used for status transitions.

    Delegates to fga_backfill's private builders so the "who survives which status"
    policy lives in one place. Not calling the script's argparse entrypoint — we
    want to reuse the same client and the same app context that's already active.
    """
    # Local import: keeps startup independent of the script's argparse module.
    from auth_api.scripts.fga_backfill import (  # noqa: PLC0415
        _build_affiliation_tuples,
        _build_linking_key_tuples,
        _build_membership_tuples,
        _build_product_tuples,
    )

    current_app.logger.info("fga-sync: rematerializing org %s (%s)", org_id, event_type)
    all_tuples = (
        _build_membership_tuples(org_id)
        + _build_affiliation_tuples(org_id)
        + _build_product_tuples(org_id)
        + _build_linking_key_tuples(org_id)
    )
    # Strategy: delete-then-write for the org is not directly supported by OpenFGA
    # (no wildcard delete). For now, write the surviving set; stale tuples on demotion
    # are deleted by the emitting MEMBERSHIP_DEACTIVATED / etc events. If drift
    # accumulates, run the full backfill script.
    if all_tuples:
        client.write(ClientWriteRequest(writes=all_tuples))
