# Copyright © 2026 Province of British Columbia
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
"""OpenFGA client wrapper for authorization checks.

Realm roles from the JWT are passed as contextual tuples per request; nothing
about the caller's realm role membership is persisted in OpenFGA. Everything
else (org membership, affiliation, product subscription, linking-key delegation)
is persisted via fga-sync consuming Pub/Sub events.
"""

from openfga_sdk import ClientConfiguration, OpenFgaClient
from openfga_sdk.client.models import ClientCheckRequest, ClientListObjectsRequest, ClientTuple
from openfga_sdk.credentials import Credentials, CredentialConfiguration

from flask import current_app

from auth_api.utils.roles import Role

_ROLE_TO_SYSTEM_RELATION = {
    Role.STAFF.value: "staff",
    Role.STAFF_CREATE_ACCOUNTS.value: "staff_admin",
    Role.SYSTEM.value: "system_account",
    Role.EXTERNAL_STAFF_READONLY.value: "external_staff_ro",
    Role.GOV_ACCOUNT_USER.value: "gov_account_user",
}


class FgaService:
    """Thin wrapper over openfga-sdk that injects realm-role contextual tuples."""

    _client: OpenFgaClient | None = None

    @classmethod
    def _get_client(cls) -> OpenFgaClient:
        """Return a memoised sync client bound to the configured store/model."""
        if cls._client is not None:
            return cls._client

        api_url = current_app.config.get("OPENFGA_API_URL")
        store_id = current_app.config.get("OPENFGA_STORE_ID")
        model_id = current_app.config.get("OPENFGA_MODEL_ID")
        api_token = current_app.config.get("OPENFGA_API_TOKEN")

        credentials = None
        if api_token:
            credentials = Credentials(
                method="api_token",
                configuration=CredentialConfiguration(api_token=api_token),
            )

        config = ClientConfiguration(
            api_url=api_url,
            store_id=store_id,
            authorization_model_id=model_id,
            credentials=credentials,
        )
        cls._client = OpenFgaClient(config)
        return cls._client

    @classmethod
    def reset(cls) -> None:
        """Drop the memoised client. Used in tests and after config reload."""
        cls._client = None

    @staticmethod
    def _build_realm_role_tuples(user_guid: str, jwt_roles: list[str] | None) -> list[ClientTuple]:
        """Translate realm roles from the JWT into system:global contextual tuples."""
        if not jwt_roles:
            return []
        user_ref = f"user:{user_guid}"
        return [
            ClientTuple(user=user_ref, relation=relation, object="system:global")
            for role, relation in _ROLE_TO_SYSTEM_RELATION.items()
            if role in jwt_roles
        ]

    @classmethod
    def check(
        cls,
        user_guid: str,
        relation: str,
        object_type: str,
        object_id: str,
        jwt_roles: list[str] | None = None,
    ) -> bool:
        """Return True if the user has the relation on the object.

        Realm roles from the JWT are passed as contextual tuples so the FGA model
        can grant STAFF-level escalation without a Keycloak → OpenFGA sync.
        """
        try:
            client = cls._get_client()
            request = ClientCheckRequest(
                user=f"user:{user_guid}",
                relation=relation,
                object=f"{object_type}:{object_id}",
                contextual_tuples=cls._build_realm_role_tuples(user_guid, jwt_roles),
            )
            response = client.check(request)
            return bool(response.allowed)
        except Exception as exc:  # pylint: disable=broad-except
            # Shadow-mode callers still get a definitive False; SQL path remains authoritative
            # until the enable-openfga-authz flag is flipped for the endpoint.
            current_app.logger.error("FgaService.check failed: %s", exc)
            return False

    @classmethod
    def list_objects(
        cls,
        user_guid: str,
        relation: str,
        object_type: str,
        jwt_roles: list[str] | None = None,
    ) -> list[str]:
        """Return the object IDs of the given type where the user has the relation."""
        try:
            client = cls._get_client()
            request = ClientListObjectsRequest(
                user=f"user:{user_guid}",
                relation=relation,
                type=object_type,
                contextual_tuples=cls._build_realm_role_tuples(user_guid, jwt_roles),
            )
            response = client.list_objects(request)
            prefix = f"{object_type}:"
            return [obj.removeprefix(prefix) for obj in response.objects]
        except Exception as exc:  # pylint: disable=broad-except
            current_app.logger.error("FgaService.list_objects failed: %s", exc)
            return []
