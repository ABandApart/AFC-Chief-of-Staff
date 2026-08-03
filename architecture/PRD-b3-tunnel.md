# B3: Exposure — Authenticated Tunnel + Ingest API — PRD & Build Spec

<doc:meta>
  <doc:phase>B3 (trust boundary) — foundation for all inbound</doc:phase>
  <doc:theme>"From anywhere" = an authenticated API via a tunnel; the DB never leaves the box</doc:theme>
  <doc:duration>~2–3 days</doc:duration>
  <doc:owner>Barry Baldwin</doc:owner>
  <doc:status>drafted — build next (with B2), before Phase 6 (lead-gen webhook)</doc:status>
  <doc:depends_on>`_lib/ingest.ingest_note`; `configure_cognee`; keychain creds</doc:depends_on>
  <doc:blocks>Phase 6 (WordPress lead webhook), Phase 15 (inbound email), the primary API ingestion channel</doc:blocks>
</doc:meta>

## TL;DR

Stand up a small **authenticated HTTP API** on the Mac mini, reachable from the
internet through a **Cloudflare Tunnel** — *no inbound ports opened, Postgres
stays on the local socket*. The API's first job is the **ingest endpoint** (the
"API + tools are the primary ingestion channel" the operator called out): an
authenticated `POST /ingest` that runs `ingest_note`. It's also the receiver for
the Phase-6 WordPress lead webhook. This is trust boundary **B3**
(`25-target-state.md`); every inbound channel rides on it.

## Goal & Non-Goals

**Goal:** an external caller (a tool, a phone shortcut, the WordPress site) can
reach an authenticated endpoint on the mini; the request is validated, written to
the graph via the existing ingest core, and the DB is never internet-reachable.

**Non-goals:** no public/unauthenticated surface; no user accounts/OAuth *for the
API itself* (that's per-integration, e.g. Google OAuth for Drive); Postgres is
**never** exposed — the tunnel only fronts the app port.

## Design

### Components
1. **The API app** — `agents/gateway/app.py`, a small **FastAPI** app (net-new;
   the repo has no web server today — `fastapi`/`uvicorn` come transitively via
   the `cognee` group but are unused, so add them as explicit deps in a new
   `gateway` dependency group). Endpoints (v1):
   - `GET /health` — liveness (no auth).
   - `POST /ingest` — auth required; body `{text, source_ref, source_type}` →
     **ack-then-process**: validate + enqueue, return `202` fast, run
     `ingest_note(...)` in the background (per the refactor proposal's
     ack-then-process note). Untrusted input → **B1 still applies** (the text is
     data, cognee treats it as inert).
   - `POST /webhook/leads` — auth required; the Phase-6 WordPress payload →
     `prospects` (Phase 6 builds the handler; the route + auth land here).
   Run under launchd (`com.aiadaptive.cos.gateway.plist`, KeepAlive), on
   `127.0.0.1:<port>` only.
2. **The tunnel** — `cloudflared` (brew), a named tunnel routing
   `https://<hostname>` → `127.0.0.1:<port>`, run as its own launchd service. No
   firewall ports opened; the mini makes an **outbound** connection to Cloudflare.
3. **Auth** (the key decision — recommend both layers):
   - **Machine callers** (WordPress, tools): a **shared-secret HMAC** signature
     header the app verifies (secret in keychain, `gateway-hmac-secret`), plus an
     allow-listed `source_type`. Simple, no Cloudflare-account coupling in code.
   - **Cloudflare Access** in front (optional, operator-configured) for
     browser/human routes and defense-in-depth. Service tokens for machine callers
     if Access is used.

### Why Cloudflare Tunnel (vs Tailscale Funnel)
The lead webhook needs a caller *outside your tailnet* (the WordPress host) to
POST in. Cloudflare Tunnel fronts a stable public hostname with per-route auth and
service tokens — the right fit for arbitrary external webhook callers. Tailscale
Funnel is simpler but tailnet-centric (great for personal-device access, awkward
for a third-party site). Recommendation: **Cloudflare Tunnel + HMAC on the
webhook**, Access optional.

### Trust-boundary rules baked in
- The tunnel fronts **only** the app port; Postgres listens on the local socket,
  never routed.
- Every route except `/health` is authenticated; unauthenticated/failed-HMAC →
  `401`, nothing touches the graph.
- `POST /ingest` content crosses **B1** into the graph exactly like Discord/Granola
  (it's data, not instructions) — same `ingest_note`, same `capture`/channel
  dataset, same telemetry label.

## Build outline

1. `gateway` dep group (`fastapi`, `uvicorn[standard]`); `agents/gateway/app.py`
   (health + ingest + leads-route stub), HMAC-verify dependency, ack-then-process
   background task calling `ingest_note`.
2. `launchd/com.aiadaptive.cos.gateway.plist` (KeepAlive, `uvicorn` on localhost).
3. `cloudflared` install + named tunnel + `launchd` service; hostname routing.
4. Tests (pure): HMAC verify (valid/invalid/missing), request-body validation,
   the ack-then-process handoff (mock ingest). Runtime smoke: external `curl`
   with a signed body → `202` → note appears in the graph; unsigned → `401`.

## Human / credential actions (operator — I can't do these)
- Cloudflare account + a named tunnel + DNS hostname + tunnel credential on the
  mini; (optional) Cloudflare Access policy + service token.
- Provision `gateway-hmac-secret` in barry-agent's keychain; put the same secret
  in the WordPress webhook config.

## Open decisions (recommend, confirm at build)
- **FastAPI vs stdlib `http.server`:** FastAPI (recommended — validation + auth
  deps + async background tasks are worth the one dep group; it's ARM-wheel clean).
- **Auth:** HMAC shared-secret on machine routes (recommended) ± Cloudflare Access.
- **Hostname/domain:** operator's call (a subdomain they control on Cloudflare).
