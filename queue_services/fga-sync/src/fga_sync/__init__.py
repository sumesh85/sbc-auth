# Copyright © 2026 Province of British Columbia
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
"""Consumer service that translates auth-api mutation events into OpenFGA tuple ops."""

import os

from auth_api.exceptions import ExceptionHandler
from auth_api.models import db
from auth_api.services.flags import flags
from auth_api.services.gcp_queue import queue
from flask import Flask

from fga_sync import config as app_config
from fga_sync.resources.worker import bp as worker_endpoint


def create_app(run_mode: str | None = None) -> Flask:
    """Return a configured Flask app for the fga-sync consumer."""
    if run_mode is None:
        run_mode = os.getenv("DEPLOYMENT_ENV", "production")
    app = Flask(__name__)
    app.config.from_object(app_config.get_named_config(run_mode))
    app.url_map.strict_slashes = False

    db.init_app(app)
    flags.init_app(app)
    queue.init_app(app)

    app.register_blueprint(worker_endpoint, url_prefix="/")
    ExceptionHandler(app)
    return app
