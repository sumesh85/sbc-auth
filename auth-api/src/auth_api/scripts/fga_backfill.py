# Copyright © 2026 Province of British Columbia
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
"""Materialize OpenFGA tuples from SQL for initial load and recovery.

Two use cases:
1. Bootstrap: walk every active membership / affiliation / product-sub / linking-key
   in the DB and write the corresponding tuples to OpenFGA. Run once per environment
   when adopting FGA, and to seed a freshly-provisioned store.
2. Recovery on org un-suspension: when an org transitions back to ACTIVE from
   SUSPENDED or NSF_SUSPENDED, re-materialize its tuples in one pass (--org-id).

Usage:
    poetry run python -m auth_api.scripts.fga_backfill --dry-run
    poetry run python -m auth_api.scripts.fga_backfill --org-id 1234
    poetry run python -m auth_api.scripts.fga_backfill --confirm
"""

from __future__ import annotations

import argparse
import os
import sys

from openfga_sdk.client.models import ClientTuple, ClientWriteRequest

from auth_api import create_app
from auth_api.models import Affiliation as AffiliationModel
from auth_api.models import Membership as MembershipModel
from auth_api.models import Org as OrgModel
from auth_api.models import ProductSubscription as ProductSubscriptionModel
from auth_api.models.account_linking_key import AccountLinkingKey as LinkingKeyModel
from auth_api.services.fga_service import FgaService
from auth_api.utils.enums import LinkingKeyStatus, OrgStatus, ProductSubscriptionStatus, Status
from auth_api.utils.roles import ADMIN, COORDINATOR, USER

_ROLE_RELATION = {
    ADMIN: "admin",
    COORDINATOR: "coordinator",
    USER: "basic_member",
}

# Membership types that survive each org status. Everything not in the set has its tuples dropped.
_MEMBERSHIP_BY_STATUS = {
    OrgStatus.ACTIVE.value: {ADMIN, COORDINATOR, USER},
    OrgStatus.NSF_SUSPENDED.value: {ADMIN},
    OrgStatus.SUSPENDED.value: set(),
}


def _build_membership_tuples(org_id: int | None) -> list[ClientTuple]:
    """org#<role>@user:<guid> tuples for every ACTIVE membership in a surviving org status."""
    query = MembershipModel.query.filter(MembershipModel.status == Status.ACTIVE.value).join(OrgModel)
    if org_id is not None:
        query = query.filter(MembershipModel.org_id == org_id)
    tuples: list[ClientTuple] = []
    for m in query.all():
        surviving_roles = _MEMBERSHIP_BY_STATUS.get(m.org.status_code, set())
        if m.membership_type_code not in surviving_roles:
            continue
        relation = _ROLE_RELATION.get(m.membership_type_code)
        if not relation or not m.user or not m.user.keycloak_guid:
            continue
        tuples.append(
            ClientTuple(
                user=f"user:{m.user.keycloak_guid}",
                relation=relation,
                object=f"org:{m.org_id}",
            )
        )
    return tuples


def _build_affiliation_tuples(org_id: int | None) -> list[ClientTuple]:
    """entity:<business_identifier>#affiliated_org@org:<id> tuples."""
    query = AffiliationModel.query.join(AffiliationModel.entity)
    if org_id is not None:
        query = query.filter(AffiliationModel.org_id == org_id)
    return [
        ClientTuple(
            user=f"org:{a.org_id}",
            relation="affiliated_org",
            object=f"entity:{a.entity.business_identifier}",
        )
        for a in query.all()
        if a.entity and a.entity.business_identifier
    ]


def _build_product_tuples(org_id: int | None) -> list[ClientTuple]:
    """product:<code>#subscribed_org@org:<id> tuples for ACTIVE subscriptions."""
    query = ProductSubscriptionModel.query.filter(
        ProductSubscriptionModel.status_code == ProductSubscriptionStatus.ACTIVE.value
    )
    if org_id is not None:
        query = query.filter(ProductSubscriptionModel.org_id == org_id)
    return [
        ClientTuple(
            user=f"org:{p.org_id}",
            relation="subscribed_org",
            object=f"product:{p.product_code}",
        )
        for p in query.all()
    ]


def _build_linking_key_tuples(org_id: int | None) -> list[ClientTuple]:
    """org:<source>#delegate@org:<vendor>#member for ACTIVE linking keys."""
    query = LinkingKeyModel.query.filter(LinkingKeyModel.status == LinkingKeyStatus.ACTIVE.value)
    if org_id is not None:
        # Include keys where this org is either source or vendor
        query = query.filter(
            (LinkingKeyModel.account_id == org_id) | (LinkingKeyModel.vendor_account_id == org_id)
        )
    return [
        ClientTuple(
            user=f"org:{k.vendor_account_id}#member",
            relation="delegate",
            object=f"org:{k.account_id}",
        )
        for k in query.all()
        if k.vendor_account_id
    ]


def _chunk(items: list[ClientTuple], size: int = 100) -> list[list[ClientTuple]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def main(argv: list[str] | None = None) -> int:
    """Entry point for `python -m auth_api.scripts.fga_backfill`."""
    parser = argparse.ArgumentParser(description="Materialize OpenFGA tuples from SQL state.")
    parser.add_argument("--org-id", type=int, help="Restrict backfill to a single org (used for recovery)")
    parser.add_argument("--dry-run", action="store_true", help="Print counts without writing to OpenFGA")
    parser.add_argument("--confirm", action="store_true", help="Required for non-dry-run writes")
    args = parser.parse_args(argv)

    app = create_app(run_mode=os.getenv("DEPLOYMENT_ENV", "production"))
    with app.app_context():
        membership_tuples = _build_membership_tuples(args.org_id)
        affiliation_tuples = _build_affiliation_tuples(args.org_id)
        product_tuples = _build_product_tuples(args.org_id)
        linking_key_tuples = _build_linking_key_tuples(args.org_id)

        all_tuples = membership_tuples + affiliation_tuples + product_tuples + linking_key_tuples

        print(f"membership tuples:   {len(membership_tuples)}")  # noqa: T201
        print(f"affiliation tuples:  {len(affiliation_tuples)}")  # noqa: T201
        print(f"product tuples:      {len(product_tuples)}")  # noqa: T201
        print(f"linking-key tuples:  {len(linking_key_tuples)}")  # noqa: T201
        print(f"TOTAL:               {len(all_tuples)}")  # noqa: T201

        if args.dry_run:
            return 0

        if not args.confirm:
            print("Refusing to write without --confirm. Re-run with --confirm or --dry-run.", file=sys.stderr)  # noqa: T201
            return 2

        client = FgaService._get_client()  # noqa: SLF001 — script-only usage
        written = 0
        for batch in _chunk(all_tuples, 100):
            client.write(ClientWriteRequest(writes=batch))
            written += len(batch)
            print(f"wrote {written}/{len(all_tuples)}")  # noqa: T201
        return 0


if __name__ == "__main__":
    sys.exit(main())
