# fga-sync

Consumer for the `FGA_SYNC_TOPIC` GCP Pub/Sub topic. Translates authorization
mutation events emitted by `auth-api` (via `auth_api.utils.fga_publisher`) into
OpenFGA tuple `Write` / `Delete` operations.

Events consumed:
- `bc.registry.auth.fga.membershipActivated` / `.membershipRoleChanged` / `.membershipDeactivated`
- `bc.registry.auth.fga.affiliationCreated` / `.affiliationRemoved`
- `bc.registry.auth.fga.productSubscribed` / `.productUnsubscribed`
- `bc.registry.auth.fga.orgSuspended` / `.orgNsfSuspended` / `.orgActivated`
- `bc.registry.auth.fga.linkingKeyActivated` / `.linkingKeyRevoked`

Deploy target: GCP Cloud Run, one instance per environment
(`fga-sync-{dev,test,sandbox,prod}`), subscribed to the corresponding
`fga-sync-{env}` topic via push subscription.

For the org-activated event (recovery from suspension), the consumer
re-materializes tuples for the affected org by calling into
`auth_api.scripts.fga_backfill` scoped by `--org-id`.
