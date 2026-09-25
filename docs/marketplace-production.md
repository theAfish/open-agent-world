# Official Marketplace readiness

The requested production implementation is prepared. **The official service is
not deployed**, and no production URL has been invented. The operator confirmed
there is no current VPS/domain and requested a stop before provisioning resources,
DNS, certificates or production secrets. Those steps remain external.

## Implementation

The independent `theAfish/oaw-marketplace` repository contains the single supported
path: VPS + Docker Compose + PostgreSQL 17 + Caddy HTTPS. Its
`docs/production-runbook.md` is the operational source of truth; start with
`docs/deployment.md`. It includes explicit Alembic migration/check, migration/seed/
read-only runtime roles, persistent volumes, bounded streaming, safe JSON logs,
backups/restore, immutable original Greeter seed and anonymous production smoke.

Production uses a GitHub App installed only on `theAfish/oaw-marketplace`, with
Contents: read. The Marketplace signs RS256 JWTs from deployment secrets, exchanges
and caches short-lived installation tokens, and refreshes before expiry. Static
tokens are forbidden in production. No credentials or Marketplace server are
added to OAW; no user login, Webhook or publishing workflow was introduced.

OAW now resolves `OPEN_AGENT_WORLD_MARKETPLACE_URL` when explicitly present,
otherwise the single `OFFICIAL_MARKETPLACE_URL` in `backend/official_marketplace.py`.
An empty override explicitly disables Store for development tests. The official
constant remains `None` until a real deployment passes smoke. Tagged Desktop
releases run `scripts/check-marketplace-release.py` to prevent shipping an unbound
official Store. No user-facing URL preference or startup network check was added.

## Operator handoff

1. Supply a maintained Linux VPS with Docker/Compose, persistent disk, admin access
   and an encrypted off-host backup destination. Configure NTP and firewall ports.
2. Confirm an owned hostname, set DNS to the VPS and provide an ACME contact email.
3. Create/install the Contents-read-only GitHub App for the one private repository.
   Supply App ID, Installation ID and private PEM through protected deployment
   secrets. Configure four separate DB role passwords. Do not paste secrets into
   chat or commit them; use the Marketplace runbook's protected env-file example.
4. Deploy the reviewed Marketplace revision using `sh deploy/deploy.sh --seed-greeter`.
   The existing Greeter is downloaded and verified, never rebuilt or overwritten.
5. Run `scripts/production_smoke.py` against the public HTTPS hostname, restart
   API/DB in a planned window, repeat smoke, and exercise backup/restore.
6. Set OAW's official constant to that verified origin, rebuild, and perform the
   default-endpoint acceptance below. No Marketplace override is supplied to users.

The original Greeter is 4191 bytes, SHA256
`34ee377564f6fd7776a041cc8db82d6e9f8e5bd107c4b9287f416122259cffb5`.

## Acceptance commands and boundaries

After deployment and URL binding, with the Marketplace override absent:

```powershell
python scripts/check-marketplace-release.py
node frontend/scripts/run-pack-e2e.mjs --store-official
```

For a newly rebuilt Desktop payload:

```powershell
$env:OAW_PACK_DESKTOP_PAYLOAD = (Resolve-Path .open-agent-world/desktop-payload).Path
node frontend/scripts/run-pack-e2e.mjs --store-official
```

The runner inspects the actual source/payload default, rejects even an empty
Marketplace override, creates a fresh profile, uses the original remote digest,
and runs Store -> Get -> restart -> Pack opening -> Card Library -> Deck -> World
-> Greet -> reload/persisted greeting. It does not rebuild the Greeter. A receipt
records the production endpoint and absence of the override only after success.

This is production-host/browser acceptance; it is not native WebView acceptance.
For native acceptance, build/install through the existing Tauri/NSIS flow and
repeat the same user flow in the installed app. The Windows Computer Use runtime
is currently unavailable; no installer/native smoke pass is claimed.

Current validation and the full 16-point implementation status are in the
Marketplace repository's `docs/production-acceptance.md`. Official smoke and
official Store E2E remain pending the operator resources above. Windows code/tests
and Linux Docker service checks do not establish macOS/Linux Desktop acceptance.

The distribution endpoint authenticates the source, but Packs remain trusted
application code. No sandbox, verified publisher or cryptographic signature is
claimed. After operational acceptance, the next phase should begin with separate
account/namespace ownership contracts at the existing catalog boundary.
