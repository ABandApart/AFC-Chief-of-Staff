# B3: Exposure — Authenticated Tunnel + Ingest API — PRD & Build Spec

<doc:meta>
  <doc:phase>B3 (trust boundary) — foundation for all inbound</doc:phase>
  <doc:theme>"From anywhere" = an authenticated API via a tunnel; the DB never leaves the box</doc:theme>
  <doc:duration>~2–3 days</doc:duration>
  <doc:owner>Barry Baldwin</doc:owner>
  <doc:status>VERIFIED 2026-08-10 — barry-agent runtime-tested the Cloudflare Tunnel end-to-end (authenticated `/ingest` reachable through the tunnel; DB never left the local socket). Gateway is live. Unblocks the remote transport for the MCP tool layer (`PRD-mcp-tool-layer.md`) and Phase 6 (lead-gen webhook).</doc:status>
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
     > **Superseded by Amendment 1 (as-built).** The single `gateway-hmac-secret`
     > became **per-caller** secrets (`gateway-hmac-wordpress` / `-shortcut` /
     > `-tools`), and the signature now covers the `(timestamp, caller, body-hash)`
     > tuple over `X-AIA-*` headers — see Amendment 1 for the wire format, caps,
     > and verification order. The shipped code (`agents/gateway/`) matches the
     > amendment, not this bullet.
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
- Provision the **per-caller** HMAC secrets in barry-agent's keychain
  (`gateway-hmac-wordpress` / `-shortcut` / `-tools`, per Amendment 1); put the
  matching secret in each caller's config (e.g. the `wordpress` one in the
  WordPress webhook). Each caller sends its id in `X-AIA-Caller`.

## Amendment 1: HMAC hardening — the recommendation (2026-08-08)

<amendment id="A1" name="Request authentication hardening">

### Direct answer: is timestamp + rate limit sufficient?

**For replay — yes, and mostly for a reason the original review under-weighted.**
This corrects `REVIEW-2026-08-08-architecture-eval.md` SEC-1, which rated replay
**high**; the correct rating against this system is **low**, and a nonce cache is
**not** recommended.

The reasoning, because it should be checkable rather than taken on trust:

1. **A signed request cannot be mutated without the secret.** So the only
   available replay is a *byte-identical* one.
2. **Byte-identical replays already no-op**, on both routes, by mechanisms that
   already exist:
   - `POST /ingest` → `capture_messages.content_hash` is UNIQUE and checked
     **pre-LLM** (migration 0003). Identical text is skipped before any cognify
     spend.
   - `POST /webhook/leads` → `prospects_wp_idx` is UNIQUE on
     `wordpress_profile_id`. An identical lead payload upserts, it does not
     duplicate.
3. Therefore a replay flood costs HTTP handling and a hash lookup — not spend,
   not graph poisoning. **Rate limiting is sufficient to bound it**, and the
   timestamp is defense-in-depth rather than the load-bearing control.

**A nonce cache is explicitly not recommended.** It buys protection the dedup
layers already provide, at the cost of new shared state (a table or Redis) with
its own eviction, growth, and failure semantics. Adding stateful infrastructure
to defend an already-idempotent endpoint is the kind of complexity this
architecture's own principles reject.

> **Re-add condition (falsifiable):** if a future route is added whose handler is
> **not** idempotent — anything that appends rather than upserts, or that costs
> money per call — that route needs a nonce cache before it ships. The
> idempotency argument is per-route, not global. Record it in the route's own
> spec.

### What the original review got wrong, and what it should have led with

The severity ranking was mis-ordered. Corrected, highest-value first:

| # | Control | Why it ranks here | Effort |
|---|---------|-------------------|--------|
| **1** | **Body size cap** | **Nothing else in the system covers this.** One 500KB "note" is a single giant cognify fan-out *inside one invocation* — and the breaker is post-hoc, so it blocks the *next* call while this one has already run. Dedup does not help: an oversized note is novel content. This is the only item that is both uncovered and expensive. | 1 line |
| **2** | **Constant-time comparison** | `hmac.compare_digest`, never `==`. A naive comparison leaks the expected signature bytewise under timing analysis, which is a *key-recovery* path — categorically worse than replay. Easy to get wrong, trivial to get right. | 1 line |
| **3** | **Per-caller secrets** | WordPress compromise is a *likely* event (public PHP surface, plugin supply chain), and today one secret authenticates every caller. Separate `gateway-hmac-wordpress` / `-shortcut` / `-tools` means a WP compromise rotates one key and revokes one caller, instead of re-keying every integration at once. Also gives per-caller attribution in logs. | ~10 lines |
| **4** | **Rate limit** | Bounds flooding. Cloudflare WAF rules, free tier, configured not coded — no app change. | config |
| **5** | **Timestamp in the signed payload** | Bounds the useful life of a captured request to the skew window. Cheap, correct, but — per above — not load-bearing here. | ~5 lines |
| — | ~~Nonce cache~~ | **Skipped**, see re-add condition above. | — |

### Concrete spec

**Signature.** Sign the tuple, not just the body:

```
signing_string = f"{timestamp}\n{caller_id}\n{sha256(raw_body).hexdigest()}"
signature      = hmac.new(secret_for(caller_id), signing_string, sha256).hexdigest()
```

Headers: `X-AIA-Timestamp`, `X-AIA-Caller`, `X-AIA-Signature`.

Including `caller_id` in the signing string prevents a valid signature from one
caller being presented as another's. Hashing the body keeps the comparison
constant-size regardless of payload.

**Verification order matters** — reject cheapest-first, so an attacker cannot
make the box do work before authenticating:

1. `Content-Length` > cap → **413**, before reading the body
2. Unknown `X-AIA-Caller` → **401**
3. `|now - timestamp| > 300s` → **401**
4. `hmac.compare_digest` fails → **401**
5. Body schema validation → **422**
6. Only now: enqueue, return **202**

**Caps.** `/ingest` 32KB, `/webhook/leads` 16KB — enforced as
`Content-Length` rejection *and* a read cap (a lying `Content-Length` must not
be able to stream past it). Both are generous for their purpose: 32KB is a very
long note.

**Also:** `/health` stays unauthenticated but must return a **static** literal —
no version string, no DB status, no queue depth. An unauthenticated liveness
probe that reports internal state is a free reconnaissance endpoint.

</amendment>

## Amendment 2: exposure posture — split by audience (2026-08-08)

<amendment id="A2" name="Two exposure mechanisms, chosen by caller type">

### The rule

> **Cloudflare Tunnel fronts machine callers that cannot join the tailnet.
> Tailscale Serve fronts every human surface. Default human surface = Tailscale.**

To answer the question directly: **yes — Tailscale becomes the default mechanism
for anything a human opens in a browser.** Cloudflare does not go away; it keeps
the job it is genuinely better at.

### Why split rather than pick one

The original PRD chose Cloudflare on a real constraint: *the WordPress host is
outside the tailnet and must POST in.* That reasoning is correct and unchanged —
Tailscale Funnel is awkward for arbitrary third-party callers, and a stable
public hostname with per-route auth is exactly right for a webhook.

But that reasoning **only covers machine callers**. Applying it to the operator's
own UI imports a cost with no matching benefit:

**Cloudflare terminates TLS at their edge.** Every NocoDB page view, prospect
record, packet body, and grid edit is plaintext at a third party. That directly
contradicts the stated posture in `30-memory-layer.md` — *"client transcript text
never leaves the box"* — and the confidentiality promise in `25-target-state.md`
§8. The system takes deliberate care to keep embeddings local so client text
never reaches a provider, then would route the entire prospect database through
someone else's TLS terminator to look at a grid.

**Tailscale Serve is end-to-end WireGuard.** No third party sees the traffic, no
public hostname exists to be found, and there is no TLS-terminating middlebox.
Auth becomes device identity — which is *stronger* than a login page, and removes
the Cloudflare Access configuration surface that R4 depends on being right.

The trade is that the operator's phone and laptop must run Tailscale. For a
single-operator system that is one app, once.

### Assignment

| Surface | Audience | Mechanism | Note |
|---------|----------|-----------|------|
| `POST /webhook/leads` | WordPress (third-party machine) | **Cloudflare Tunnel + HMAC** | Cannot join the tailnet. This is the whole reason Cloudflare is here. |
| `POST /ingest` | Tools, scripts, shortcuts | **Tailscale**, Cloudflare only if a caller genuinely cannot join | Most callers are the operator's own devices. |
| `/shortcut/*` (Track O) | Operator's phone | **Tailscale** | Already an operator device. Also lets the scoped HMAC secret (R12) act as a second factor rather than the only one. |
| **NocoDB outreach grid** | Operator only | **Tailscale Serve** | Removes the public hostname entirely. |
| Future admin/status pages | Operator only | **Tailscale Serve** | Default for anything new and human-facing. |
| `GET /health` | Monitoring | **Cloudflare**, static response | See A1. |

### What this changes downstream

- **R4 (`35-outreach-crm.md`) largely dissolves.** NocoDB stops having a public
  hostname, so the "shared view link reachable by anyone with the URL" risk needs
  a tailnet foothold first. The three hardening steps stay (dedicated role,
  shared views disabled, version floor ≥2026.5.1) — defence in depth, and they
  cost nothing — but the risk drops from **High** to **Low**, and the
  Cloudflare Access policy that was load-bearing becomes optional.
- **B3's boundary statement should be read as "authenticated exposure," not
  "Cloudflare."** The invariant that matters is unchanged and holds under both:
  *no inbound ports, Postgres on the local socket, never internet-reachable.*
- **Operational cost:** two things to keep alive instead of one. Accepted — they
  fail independently, which is a feature: a Cloudflare outage does not take out
  the operator's ability to work the pipeline, and a tailnet problem does not
  drop the lead webhook.

### Build delta

Small, and mostly subtraction: `tailscale serve https / http://127.0.0.1:<nocodb-port>`
plus the same for the gateway on the tailnet interface; Cloudflare's route table
narrows to `/webhook/leads` and `/health`. No application code changes — the
gateway still binds `127.0.0.1` and still verifies HMAC on machine routes.

</amendment>

---

## Open decisions (recommend, confirm at build)
- **FastAPI vs stdlib `http.server`:** FastAPI (recommended — validation + auth
  deps + async background tasks are worth the one dep group; it's ARM-wheel clean).
- **Auth:** HMAC shared-secret on machine routes (recommended) ± Cloudflare Access.
- **Hostname/domain:** operator's call (a subdomain they control on Cloudflare).
