# auth-api-prod — Traffic & Latency Report
**Period:** 2026-05-18 → 2026-05-25 (7 days)  
**Source:** log-based metrics `auth_api_request_count` / `auth_api_request_latency`

---

## Traffic — Top Endpoints by Average RPS

| Endpoint | avg RPS |
|---|---|
| GET `/api/v1/orgs/{id}` | 0.2688 |
| GET `/api/v1/users/{id}/settings` | 0.1719 |
| POST `/api/v1/users` | 0.1375 |
| GET `/api/v1/users/@me` | 0.1322 |
| GET `/api/v1/users/{id}/notifications` | 0.1317 |
| GET `/api/v1/orgs/{id}/authorizations` | 0.1250 |
| GET `/api/v1/users/settings` | 0.1096 |
| GET `/api/v1/users/orgs` | 0.1034 |
| GET `/api/v1/entities/authorizations` | 0.0955 |
| GET `/api/v1/users/notifications` | 0.0854 |
| GET `/api/v1/orgs/{id}/products` | 0.0817 |
| GET `/api/v1/accounts/{id}/authorizations` | 0.0517 |
| GET `/api/v1/users/membership` | 0.0510 |
| OPTIONS `/api/v1/users/{id}/settings` | 0.0498 |
| OPTIONS `/api/v1/users/@me` | 0.0482 |
| OPTIONS `/api/v1/users/{id}/notifications` | 0.0478 |
| GET `/api/v1/users` | 0.0467 |
| GET `/api/v1/users/contacts` | 0.0432 |
| OPTIONS `/api/v1/users` | 0.0417 |
| GET `/api/v1/orgs` | 0.0406 |

---

## Latency — Top Endpoints by p99

| Endpoint | p95 | p99 |
|---|---|---|
| GET `/api/v1/orgs/{id}/affiliations` | 1.913s | **13.119s** |
| GET `/api/v1/orgs/{id}/members` | 3.978s | **11.711s** |
| PATCH `/api/v1/orgs/{id}/members` | 6.477s | **10.895s** |
| PUT `/api/v1/orgs/{id}` | 3.905s | 6.902s |
| GET `/api/v1/orgs/{id}/affiliations/search` | 2.877s | 6.422s |
| PUT `/api/v1/invitations/tokens` | 4.820s | 5.954s |
| POST `/api/v1/orgs` | 4.012s | 5.943s |
| PUT `/api/v1/tasks/{id}` | 3.874s | 5.144s |
| PATCH `/api/v1/invitations/{id}` | 2.657s | 3.945s |
| POST `/api/v1/orgs/{id}/products` | 3.240s | 3.925s |
| POST `/api/v1/invitations` | 1.998s | 3.623s |
| GET `/api/v1/orgs/{id}/invitations` | 0.754s | 3.423s |
| POST `/api/v1/orgs/{id}/affiliations` | 1.595s | 3.422s |
| POST `/api/v1/affiliationInvitations` | 2.025s | 3.420s |
| DELETE `/api/v1/orgs/{id}` | 3.226s | 3.376s |
| DELETE `/api/v1/orgs/{id}/members` | 2.995s | 3.057s |
| GET `/api/v1/affiliationInvitations` | 1.008s | 2.856s |
| DELETE `/api/v1/users/otp` | 2.502s | 2.685s |
| PATCH `/api/v1/affiliationInvitations/{id}` | 2.492s | 2.683s |
| GET `/api/v1/orgs/{id}/products` | 0.711s | 2.243s |

---

## Findings

### F1 — Org affiliations tail is extreme (p99 = 13s)
`GET /orgs/{id}/affiliations` has a p95 of 1.9s but a p99 of 13.1s — a 7× jump in the tail. This bimodal pattern points to a specific cohort of orgs (likely those with a large number of affiliations) triggering a slow DB path. The search variant (`/affiliations/search`) also hits 6.4s p99.

**Likely cause:** full table scan or N+1 query on affiliations for large orgs. Check for a missing composite index on `(org_id, status)` or similar.

### F2 — Org members read and write both slow
`GET /orgs/{id}/members` (p99 11.7s) and `PATCH /orgs/{id}/members` (p99 10.9s) are both in the top 3. The fact that read and write are both slow suggests the underlying query is the bottleneck, not locking. Likely the same root cause as F1 — member list queries without adequate indexing on large orgs.

### F3 — Highest-traffic endpoint is not in the latency top 20
`GET /orgs/{id}` is the busiest endpoint at 0.27 RPS but does not appear in the latency table, meaning it's consistently fast across the board. This is healthy.

### F4 — POST /users at 0.14 RPS is unexpectedly high for a write
`POST /api/v1/users` is the 3rd highest traffic endpoint. This endpoint typically creates or upserts a user on OIDC login. High RPS here means it's being called on every login event rather than only on first registration. Consider checking whether the caller can skip this call for already-known users.

### F5 — OPTIONS requests making top 20 (~0.05 RPS each)
Three OPTIONS (CORS preflight) endpoints appear in the top 20. These are cheap to serve but indicate the frontend is not caching CORS preflight responses. Adding `Access-Control-Max-Age` to auth-api responses would eliminate the majority of these.

### F6 — Invitation and task flows have consistently high latency
Every endpoint in the invitation/task cluster (`PUT /invitations/tokens`, `PATCH /invitations/{id}`, `PUT /tasks/{id}`, `POST /affiliationInvitations`) sits above 3.5s p99. These flows involve multiple DB writes, email sends, and keycloak calls — the latency is expected but could be reduced by moving email sends off the request path (async queue).

---

## Priority Summary

| Priority | Endpoint | Issue |
|---|---|---|
| High | GET `/orgs/{id}/affiliations` | p99 = 13s, likely missing index |
| High | GET `/orgs/{id}/members` | p99 = 12s, same root cause |
| High | PATCH `/orgs/{id}/members` | p99 = 11s, slow write |
| Medium | POST `/users` | Unexpectedly high RPS for a write |
| Low | OPTIONS endpoints | CORS preflight caching |
| Low | Invitation flows | Move email sends async |
