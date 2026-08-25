# Native Client Session Operations

This handoff describes the native Session protocol implemented by this service.
It is an operational contract, not a deployment record.  Do not treat a source
checkout or these examples as evidence that a production migration or client
rollout has happened.

## Identity and retained records

`AccountIdentity.account_id` is the durable account authority.  It is a UUIDv4
created once for each Django `User`; it is neither the username nor the Django
integer primary key.  The persisted value cannot be changed through model
`save()`.  `username` in API responses is display metadata only.

Each native `ClientSession` has its own immutable UUIDv4 `session_id`, one
immutable installation UUID binding, a creation/last-seen timestamp, and only
a SHA-256 digest of its opaque bearer credential.  The server never stores the
native bearer value itself.  The existing Django Web Session is only the
bootstrap authority; its ordinary expiry does not revoke a native Session.

Keep `AccountIdentity`, `ClientSession`, and `TraceUploadToken` records as
revocation/audit evidence.  In particular, do **not** delete native Session
rows to sign a device out or revoke it.  The native identity and Session admin
views do not permit add, change, or delete; use their actions below.  Do not
work around those permissions with direct ORM or SQL deletion.

## Native HTTP contract (v1)

The versioned machine-readable subset is
[`contracts/native-client-session-v1.json`](contracts/native-client-session-v1.json).
Each response from the three native **client** routes below, including errors
and method rejections, carries `Cache-Control: no-store`.  Internal
introspection is a Gateway-only endpoint, not a native client route; its JSON
success and error responses currently use the same no-store response helper.

The examples use non-secret sentinels only:

```text
account_id       = aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa
session_id       = bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb
installation_id  = 11111111-1111-4111-8111-111111111111
session_token    = NATIVE_SESSION_TOKEN_SENTINEL_000000000000
trace_token      = TRACE_UPLOAD_TOKEN_SENTINEL_00000000000000
csrf_token       = CSRF_TOKEN_SENTINEL
web_cookie       = WEB_SESSION_COOKIE_SENTINEL
internal_secret  = INTERNAL_GATEWAY_SECRET_SENTINEL
```

Never substitute a production bearer token, Django session cookie, CSRF value,
or `TRACE_GATEWAY_INTERNAL_SECRET` into documentation, command history, logs,
Git, tickets, or chat.

### Bootstrap a native Session

`POST /auth/api/client-session/` requires an authenticated, unexpired Django
Web Session and Django CSRF protection.  It accepts exactly this JSON object
(duplicate keys and additional keys are rejected):

```http
POST /auth/api/client-session/ HTTP/1.1
Content-Type: application/json
Cookie: __Host-ansatz_sessionid=WEB_SESSION_COOKIE_SENTINEL
X-CSRFToken: CSRF_TOKEN_SENTINEL

{"installation_id":"11111111-1111-4111-8111-111111111111","client_version":"0.17.0"}
```

The `installation_id` must be canonical lowercase UUIDv4 and `client_version`
must be a 1--64 character version string accepted by the service.  Success is
`201` with exactly:

```json
{
  "account_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
  "session_id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
  "session_token": "NATIVE_SESSION_TOKEN_SENTINEL_000000000000",
  "installation_id": "11111111-1111-4111-8111-111111111111",
  "username": "display-name-only",
  "issued_at": "2026-08-25T00:00:00+00:00"
}
```

Anonymous or expired Web Session bootstrap returns `401`
`{"detail":"authentication_required"}`.  Bad JSON/body returns `400`
`{"detail":"invalid_request"}`; a non-JSON content type returns `415`
`{"detail":"unsupported_media_type"}`.  A CSRF failure is Django's `403`
response; it is also no-store.  Do not infer an account or native Session
revocation from any bootstrap failure.

### Validate a native Session

`GET /auth/api/client-session/` requires exactly these two request headers:

```http
GET /auth/api/client-session/ HTTP/1.1
Authorization: Bearer NATIVE_SESSION_TOKEN_SENTINEL_000000000000
X-Ansatz-Installation-ID: 11111111-1111-4111-8111-111111111111
```

The installation header must be the canonical lowercase UUIDv4 originally
bound to the Session.  An active Session returns `200`:

```json
{
  "state": "active",
  "account_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
  "session_id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
  "installation_id": "11111111-1111-4111-8111-111111111111",
  "username": "display-name-only",
  "server_time": "2026-08-25T00:00:00+00:00"
}
```

Malformed, missing, unknown, or installation-mismatched credentials return
the retryable, non-terminal response below.  Network, DNS/TLS/proxy failures,
429/5xx responses, malformed responses, and a Web Session expiry are likewise
not native-Session revocations.

```json
{
  "state": "unavailable",
  "code": "invalid_session_credential",
  "retryable": true
}
```

Only this exact structured `403` response is terminal, and only when its
`account_id` and `session_id` exactly match the client's cached native
credential.  The allowed terminal wire codes are exactly
`account_disabled`, `account_revoked`, and `session_revoked`.

```json
{
  "state": "revoked",
  "code": "session_revoked",
  "account_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
  "session_id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
  "revoked_at": "2026-08-25T00:00:00+00:00",
  "retryable": false
}
```

`signed_out` is retained internally on the Session but is exposed as the
terminal `session_revoked` wire code.  A client must not sign the user out for
a `403` with unknown keys, invalid values, a non-terminal code, or mismatched
identity; that is non-terminal protocol failure rather than proof about the
cached Session.

### Remote sign-out

`DELETE /auth/api/client-session/current/` uses the same exact bearer and
installation headers as validation:

```http
DELETE /auth/api/client-session/current/ HTTP/1.1
Authorization: Bearer NATIVE_SESSION_TOKEN_SENTINEL_000000000000
X-Ansatz-Installation-ID: 11111111-1111-4111-8111-111111111111
```

For an active Session it records `signed_out`, revokes active native-bound
Trace tokens, and responds `204` with no body.  A later status request returns
the six-field terminal response with `code: "session_revoked"`.  The retryable
`401` unavailable and identity-matched terminal `403` shapes above also apply
to this route.  Other methods return `405`
`{"detail":"method_not_allowed"}` with `Allow: DELETE`.

The client clears its local credential as part of user-requested sign-out even
if this best-effort remote DELETE cannot complete.  Server operators must not
turn that into an instruction to delete a local SessionDB, attachments,
projects/profiles, conversations, or Trace outbox records.

### Native Trace credential issue

`POST /auth/api/client-session/trace-token/` is bearer-only and uses the same
two headers; it has no required JSON body:

```http
POST /auth/api/client-session/trace-token/ HTTP/1.1
Authorization: Bearer NATIVE_SESSION_TOKEN_SENTINEL_000000000000
X-Ansatz-Installation-ID: 11111111-1111-4111-8111-111111111111
```

It returns `201` for a new token or `200` when it rotated an existing active
token for that native Session.  The response shape is exactly:

```json
{
  "access_token": "TRACE_UPLOAD_TOKEN_SENTINEL_00000000000000",
  "expires_at": "2026-08-25T00:15:00+00:00",
  "expires_in": 900,
  "installation_id": "11111111-1111-4111-8111-111111111111"
}
```

This route uses the same retryable `401` unavailable and identity-matched
terminal `403` Session responses as validation.  It returns `405`
`{"detail":"method_not_allowed"}` with `Allow: POST` for other methods.
Trace-token readiness is not proof of local login state and is not a required
precondition for local conversation capability.

### Internal Trace-token introspection

Only the Trace Gateway calls `POST /internal/trace-token/introspect/`.  It
requires its configured internal secret and a JSON token body:

```http
POST /internal/trace-token/introspect/ HTTP/1.1
Content-Type: application/json
X-Ansatz-Internal-Token: INTERNAL_GATEWAY_SECRET_SENTINEL

{"token":"TRACE_UPLOAD_TOKEN_SENTINEL_00000000000000"}
```

For an active native-bound token, `200` returns:

```json
{
  "active": true,
  "token_id": "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
  "platform_user_id": "42",
  "platform_username": "display-name-only",
  "account_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
  "session_id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
  "installation_id": "11111111-1111-4111-8111-111111111111",
  "expires_at": "2026-08-25T00:15:00+00:00",
  "scope": "trace:write",
  "audience": "ansatz-trace-gateway"
}
```

`platform_user_id` and `platform_username` remain for legacy compatibility;
`account_id` is the authorization identity.  With a valid internal secret, a
refreshable inactive-token result has exactly:

```json
{
  "active": false,
  "reason": "token_expired",
  "explicit_revocation": false
}
```

The native classifications are:

| Result | `reason` | `explicit_revocation` | Meaning |
|---|---|---:|---|
| refreshable | `token_expired`, `token_rotated`, `token_revoked`, `invalid_token` | `false` | Refresh/retry the Trace credential; do not revoke local authorization. |
| terminal | `session_revoked`, `account_disabled`, `account_revoked` | `true` | Explicit retained native Session/account evidence. |

A terminal native-bound token includes identity and timestamp evidence taken
only from its retained `ClientSession` and `AccountIdentity` relationships:

```json
{
  "active": false,
  "reason": "session_revoked",
  "explicit_revocation": true,
  "account_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
  "session_id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
  "installation_id": "11111111-1111-4111-8111-111111111111",
  "revoked_at": "2026-08-25T00:00:00+00:00"
}
```

`revoked_at` is RFC3339 UTC.  A retained Session terminal reason and timestamp
are immutable authority: a later account disable/revoke does not replace an
earlier per-Session reason or time.  For a still-active Session, an account
operation records its reason and timestamp on the Session in the same
transaction before introspection can expose it.  A missing or inconsistent
native binding, or an account state without durable timestamp evidence,
returns `reason: "authentication_unavailable"` with
`explicit_revocation: false` and no identity fields.  It must not be promoted
to client revocation.

Legacy Web-session-bound tokens keep their existing active response (including
`session_id: null`) and their existing three-field inactive response.  They do
not gain native terminal identity evidence.  Malformed, unknown, expired,
rotated, or ordinary revoked Trace tokens likewise disclose no account,
Session, installation, or timestamp fields.

An invalid/missing internal secret returns `403` `{"active":false}`.  Invalid
introspection JSON returns its request error status with `{"active":false}`.
Do not expose the internal secret outside the service-to-Gateway trust boundary.

## Administrative response

The custom `/admin/` site is superuser-only.  Use one of these actions, never
row deletion:

| Action | Admin list | Result |
|---|---|---|
| `revoke_sessions` | `ClientSession` | Changes only selected, still-active native Sessions to retained `session_revoked` evidence. |
| `disable_accounts` | Django `User` | Sets selected users `is_active=False` and revokes each still-active native Session as `account_disabled`. |
| `revoke_accounts` | `AccountIdentity` | Marks the identity `revoked` and revokes each still-active native Session as `account_revoked`. |

These operations also revoke active TraceUploadTokens bound to every affected
native Session.  A selected-Session revoke is deliberately isolated from other
Sessions for that account and leaves an already-revoked selected Session's
first reason unchanged.  Account-wide actions also retain an earlier
per-Session revocation reason rather than overwrite that evidence.

If a disabled user is re-enabled through account administration, previously
revoked native Sessions remain revoked; a new Web bootstrap is required to
issue a new native Session.  There is no administrative "unrevoke" action for
an `AccountIdentity`; treat an account revoke as terminal evidence and escalate
before attempting any exceptional recovery procedure.

## Database migration, rollout, and rollback

Apply migrations forward through `history.0007_trace_token_client_session`:

```bash
rtk proxy conda run -n dl python manage.py migrate --noinput
```

- `0006_account_identity_client_session` adds AccountIdentity and ClientSession,
  then backfills one active UUID identity for each existing Django User in
  primary-key order.  Its reverse data migration is a no-op so it does not
  erase identity evidence.
- `0007_trace_token_client_session` adds nullable, protected
  `TraceUploadToken.client_session` and a retained Trace-token
  `revocation_reason` (`rotated` or `revoked`).  Existing legacy user,
  session-digest, and installation fields remain.

Coordinate the rollout in this order:

1. Deploy Gateway introspection parsing that tolerates additive fields while
   retaining its legacy response compatibility.
2. Deploy this auth service and migrate through `0007`.
3. Deploy Gateway durable identity handling with legacy identity fallback.
4. Deploy clients that use the native Session and Trace routes.
5. Retire legacy paths only after packaged-client adoption evidence.

During rollback, first stop routing new traffic to the native Session/Trace
routes and restore compatible application code only after the Gateway/client
owners have been coordinated.  Keep the database at its additive migration
state and retain all AccountIdentity, ClientSession, and TraceUploadToken rows;
do not run destructive reverse migrations or delete records.  Legacy routes
remain the compatibility path while they are still deployed:

- `GET /auth/api/session/`
- `POST /auth/api/trace-token/`
- `POST /auth/api/trace-token/revoke-device/`
- `POST /internal/trace-token/introspect/`
- `/auth/login/` and `/auth/logout/`

A client encountering a rollback-era unavailable/malformed native endpoint
must preserve its cached local authorization until user sign-out or a valid,
identity-matched terminal response.  Neither rollback nor terminal revocation
authorizes deletion of client-local SessionDB, attachments, conversations,
projects/profiles, or account-isolated Trace outbox data.  Terminal state stops
local capability/upload and preserves those records for recovery/audit.

## Executable checks

Run these from `auth-service/` after a documentation or deployment handoff:

```bash
rtk proxy conda run -n dl python manage.py test \
  history.tests.test_native_client_session_contract \
  history.tests.test_native_client_session_api \
  history.tests.test_admin_auth \
  history.tests.test_native_trace_tokens
```

The matching client fixture consumer is run from the client repository:

```bash
rtk proxy conda run -n dl bash -c \
  'export HERMES_PYTHON=/Users/yuxiaoy/miniconda3/envs/dl/bin/python; \
   bash scripts/run_tests.sh tests/hermes_cli/client_auth/test_contract_fixture.py -q'
```

These tests are the executable source for route names, response keys, terminal
codes, administrative actions, Trace classifications, and fixture compatibility.
