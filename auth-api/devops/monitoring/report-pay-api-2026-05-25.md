# pay-api-prod — Traffic & Latency Report
**Period:** 2026-05-18 → 2026-05-25 (7 days)  
**Source:** log-based metrics `pay_api_request_count` / `pay_api_request_latency`

---

## Traffic — Top Endpoints by Average RPS

| Endpoint | avg RPS |
|---|---|
| GET `/api/v1/payment-requests/{id}` | 0.1304 |
| POST `/api/v1/payment-requests` | 0.0896 |
| GET `/api/v1/fees` | 0.0576 |
| GET `/api/v1/accounts/{id}` | 0.0308 |
| OPTIONS `/api/v1/fees` | 0.0275 |
| OPTIONS `/api/v1/accounts/{id}` | 0.0150 |
| GET `/api/?/?` *(unmatched URLs — see F1)* | 0.0102 |
| POST `/api/v1/payment-requests/{id}/receipts` | 0.0100 |
| POST `/api/v1/payment-requests/{id}/transactions` | 0.0068 |
| PATCH `/api/v1/payment-requests/{id}/transactions` | 0.0064 |
| GET `/api/v1/payment-requests/{id}/receipts` | 0.0062 |
| GET `/api/v1/accounts/{id}/statements` | 0.0052 |
| POST `/api/v1/accounts/{id}/queries` | 0.0051 |
| GET `/api/v1/payment-requests` | 0.0049 |
| GET `/api/v1/codes/valid_payment_methods` | 0.0034 |
| OPTIONS `/api/v1/payment-requests/{id}` | 0.0019 |
| POST `/api/v1/accounts` | 0.0017 |
| GET `/api/v1/fas/{id}/routing-slips` | 0.0008 |
| POST `/api/v1/fas/queries` | 0.0008 |
| GET `/api/v1/codes` | 0.0008 |

---

## Latency — Top Endpoints by p99

| Endpoint | p95 | p99 |
|---|---|---|
| POST `/api/v1/accounts/{id}/queries` | 0.926s | **12.866s** |
| GET `/api/?/?` *(unmatched — see F1)* | 5.538s | **9.022s** |
| GET `/api/v1/accounts/{id}/statements` | 2.992s | 8.132s |
| GET `/api/v1/payment-requests/{id}/receipts` | 0.453s | 7.719s |
| PATCH `/api/v1/payment-requests/{id}/transactions` | 5.775s | 6.900s |
| POST `/api/v1/payment-requests/{id}/refunds` | 4.143s | 6.820s |
| POST `/api/v1/accounts/{id}/reports` | 3.213s | 6.269s |
| POST `/api/v1/bank-accounts/verifications` | 2.487s | 6.205s |
| POST `/api/v1/payment-requests/{id}/receipts` | 1.869s | 5.237s |
| GET `/api/v1/payment-requests` | 4.153s | 5.097s |
| POST `/api/v1/payment-requests` | 1.382s | 5.031s |
| OPTIONS `/api/v1/fees` | 0.007s | 4.861s |
| GET `/api/v1/accounts/{id}` | 0.975s | 4.416s |
| PATCH `/api/v1/payments/{id}/transactions` | 3.973s | 4.071s |
| OPTIONS `/api/v1/codes` | 3.590s | 4.003s |
| OPTIONS `/api/v1/fas/queries` | 2.258s | 3.735s |
| GET `/api/v1/accounts/{id}/nsf` | 0.389s | 3.605s |
| GET `/api/v1/eft-shortnames` | 1.466s | 3.403s |
| GET `/api/v1/fees` | 0.127s | 3.317s |
| POST `/api/v1/fas/{id}/reports` | 2.893s | 3.036s |

---

## Findings

### F1 — Unmatched URLs (`/api/?/?`) — needs investigation
0.0102 RPS is hitting URLs that do not match the `/api/v[12]/` pattern, so the label extractor returns null for `route_group` and `api_version`. These are bucketed together as a single unknown row with p99 = 9s, which is significant.

Run the following to identify the actual paths:
```bash
gcloud logging read \
  'resource.type="cloud_run_revision" resource.labels.service_name="pay-api-prod" httpRequest.requestUrl!="" NOT httpRequest.requestUrl=~"/api/v[12]/"' \
  --project=gtksf3-prod --freshness=7d \
  --format='value(httpRequest.requestUrl)' \
  | sort | uniq -c | sort -rn | head -30
```
Until identified, these ~1000 requests/day with 9s p99 are a blind spot.

### F2 — POST /accounts/{id}/queries has a catastrophic tail (p99 = 12.9s)
The p95 is only 0.9s but p99 jumps to 12.9s — a 14× increase. This is the most severe latency anomaly in pay-api. A pattern like this typically means one of:
- A specific subset of accounts triggers an expensive DB query (missing index for large result sets)
- DB lock contention when a report job runs concurrently with this query
- The query has no timeout and occasionally waits on an external resource

**Action:** add query-level logging for requests exceeding 2s on this endpoint to capture the exact query plan for slow cases.

### F3 — GET /payment-requests/{id}/receipts has a spike tail (p99 = 7.7s vs p95 = 0.5s)
A 15× jump from p95 to p99 is a strong signal that a small number of receipts (likely those with many line items or complex PDF generation) take drastically longer. The endpoint is low volume (0.0062 RPS) so this is a user-experience issue, not a capacity one.

### F4 — GET /fees is high-traffic and slow enough to cache (p99 = 3.3s)
`GET /fees` is the 3rd busiest endpoint at 0.06 RPS (≈ 36k requests/week). Fee schedules rarely change. A short-lived cache (60–300s TTL) in the service layer would eliminate most of these DB hits and reduce p99 significantly.

### F5 — OPTIONS requests showing high latency (p99 up to 4.9s)
Three OPTIONS (CORS preflight) endpoints appear in the latency table with p99 between 3.7s and 4.9s. OPTIONS responses should be served immediately from middleware — they should never touch the DB. This latency level on a preflight suggests the CORS handler is running after middleware that does authentication or DB lookups. Check the middleware order in pay-api.

### F6 — Statement and report generation is slow but expected
`GET /accounts/{id}/statements` (p99 8.1s), `POST /accounts/{id}/reports` (p99 6.3s), and `POST /fas/{id}/reports` (p99 3.0s) are all in the upper latency tier. These involve data aggregation and likely PDF generation, so some latency is expected. If these are user-facing synchronous calls, consider moving them to an async job with polling or a callback.

### F7 — Payment creation is the 2nd busiest endpoint at 0.09 RPS
`POST /payment-requests` is high volume for a write operation. With p99 = 5s this is on the edge — worth profiling whether the slow tail comes from external calls (BCOL, CAS, etc.) or internal DB writes.

---

## Priority Summary

| Priority | Endpoint | Issue |
|---|---|---|
| High | GET `/api/?/?` | Unknown URLs with 9s p99 — needs identification |
| High | POST `/accounts/{id}/queries` | p99 = 12.9s (14× jump from p95) |
| High | OPTIONS endpoints | 4–5s p99 on preflights — middleware ordering issue |
| Medium | GET `/payment-requests/{id}/receipts` | p99 = 7.7s tail on receipt fetch |
| Medium | GET `/fees` | High traffic, cacheable, p99 = 3.3s |
| Low | Statement/report endpoints | Long-running — consider async job pattern |
