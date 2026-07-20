# Copyright © 2026 Province of British Columbia
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
"""Pub/Sub push endpoint that receives fga-sync events and applies tuple ops."""

from http import HTTPStatus

from auth_api.services.gcp_queue import queue
from auth_api.services.fga_service import FgaService
from flask import Blueprint, current_app, request

from fga_sync.tuple_ops import apply_event

bp = Blueprint("worker", __name__)


@bp.route("/", methods=("POST",))
def worker():
    """Handle a single Pub/Sub push. Always returns 200 so the message is ack'd."""
    event = queue.get_simple_cloud_event(request, wrapped=True)
    if event is None:
        return {}, HTTPStatus.OK

    current_app.logger.info("fga-sync received event id=%s type=%s", event.id, event.type)
    try:
        client = FgaService._get_client()  # noqa: SLF001 — same-repo consumer
        apply_event(client, event.type, event.data or {})
    except Exception as exc:  # pylint: disable=broad-except
        # Ack the message either way; a failing tuple write will be caught by the
        # divergence dashboard and repaired by the next backfill run.
        current_app.logger.error("fga-sync failed to apply event %s: %s", event.type, exc)

    return {}, HTTPStatus.OK
