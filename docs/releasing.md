# Publishing desktop releases

The **Desktop release** workflow builds Windows x64, macOS Apple Silicon and macOS Intel from the same commit.

## Prepare a release

1. Prepare the version and `docs/release-notes.md` in a PR to `dev`. Use a new
   version greater than previous releases; do not reuse a published tag.
2. Run `python scripts/check-release.py --set-version 0.6.0` (or the intended
   version) to update all application versions together: frontend and desktop
   package manifests and lockfiles, backend metadata, lockfile and runtime
   version, and Tauri/Cargo configuration and lockfile. The command also accepts
   `v0.6.0` and prereleases such as `0.6.0-rc.1`, using equivalent Python metadata.
   It preserves dependency, SDK and Pack versions. Commit the resulting changes.
   Run `python scripts/check-release.py` to check consistency. Packaging verifies
   these committed values and never rewrites their versions; pushing a tag alone
   does not update application versions.
3. Promote `dev` to `main` through a merge PR for a stable release. Wait for
   **Pull request checks**, **Documentation**, and **Desktop native checks** on
   the resulting source SHA. Release gating requires the latest push/manual
   run of each workflow to succeed; an earlier green run cannot hide a later
   failure, cancellation, or pending run.
4. Run **Desktop release** manually on that commit's branch to produce preview
   installers. For a feature-branch preview, first manually run all three
   validation workflows on that branch. Manual builds also verify the combined
   artifact manifest and retain generated notes in `release-evidence`; they do
   not create a Release.
5. Test installation, first launch, model configuration, backup/restore and
   upgrade on Windows x64 and both macOS architectures. Payload self-tests do
   not replace installed-app acceptance.
6. Tag the verified commit with `vX.Y.Z` and push only that tag. Stable tags must
   be reachable from `main`; `vX.Y.Z-alpha.N`, `-beta.N`, or `-rc.N` tags must
   be reachable from `dev`. Prerelease flags are set automatically from the tag.
   All builders check out the verified SHA. Only after every build succeeds
   does the workflow create/update a draft.
7. Review the generated notes, `release-validation.json`, per-platform
   provenance, `release-manifest.json`, installers, SHA-256 files and optional
   updater files. The manifest binds versions, checks, source SHA and asset
   hashes. Complete signing and installed-app requirements before manually
   publishing the draft. No workflow publishes a formal Release automatically.
8. Merge `main` back into `dev` through a PR after stable promotion, including
   the promotion commit so subsequent release PRs remain up to date. Use merge
   commits for promotions and branch synchronization; squashing these PRs loses
   their shared history and makes already promoted work appear unmerged.

### Version domains

The OAW application version is separate from backend/frontend Plugin API major
1, SDK package `@oaw/plugin-api@1.0.0`, and each Pack's own version.
Do not bump SDK or Pack versions just to release OAW. Pack `compatibility.oaw`
is checked against the backend runtime application version. Python uses
`X.Y.ZaN`, `X.Y.ZbN`, or `X.Y.ZrcN` for the corresponding SemVer prerelease;
the version checker enforces that equivalence.

The workflow uses GitHub's automatic `GITHUB_TOKEN` with write permission only in the release job; no personal token is needed. Repository/organization policy must allow GitHub Actions and the job's `contents: write` permission. Pushing a tag through a workflow's `GITHUB_TOKEN` does not normally trigger another workflow; push the release tag from your authenticated Git client.

Failed builds keep their logs under Actions. Fix the issue before tagging a new version. Rerunning a tag workflow can replace assets in its existing draft; it refuses to change an already published Release. Never move a published version tag.

## Release modes

Manual dispatch builds all three installers without creating a Release, even when dispatched against a tag. Pushed `v*` tags build the same installers and create/update a draft only after all builds succeed. Neither mode requires signing or updater keys by default. Marketplace is checked only for pushed tags with `OAW_REQUIRE_MARKETPLACE=true`.

`OAW_SIGNED_RELEASE=true` applies to both manual and tagged builds: missing OS credentials fail closed and built installers must pass signature/notarization verification. Leave it unset or false to permit unsigned Windows and ad-hoc macOS builds. Updater keys are independent: neither key disables updates, both enable signed updater artifacts, and only one key fails preflight. The preflight checks key presence, not cryptographic correspondence; retain a matching generated pair and verify update acceptance before publication. Invalid credentials still fail during import/signing/notarization.

## Signing and automatic updates

The native desktop menu has **Check for updates**. Builds with a configured updater check once after startup; offline checks do not interrupt startup. The user approves download and later approves installation. Downloads are verified by Tauri's updater signature before the backend is stopped. The UI does not expose native updater commands to hosted pages or plugin JavaScript.

Set these repository values before enabling this distribution channel:

| Location | Name | Value |
| --- | --- | --- |
| Variable | `OAW_UPDATER_PUBLIC_KEY` | Public key from `npx tauri signer generate` in `desktop` |
| Secret | `TAURI_SIGNING_PRIVATE_KEY` | Matching private signing key, backed up securely |
| Secret | `TAURI_SIGNING_PRIVATE_KEY_PASSWORD` | Signing key password, if set |
| Secret | `WINDOWS_CERTIFICATE` | Base64 code-signing PFX supported by the Windows certificate store |
| Secret | `WINDOWS_CERTIFICATE_PASSWORD` | PFX password |
| Secret | `APPLE_CERTIFICATE` | Base64 Developer ID Application P12 |
| Secret | `APPLE_CERTIFICATE_PASSWORD` | P12 password |
| Variable | `APPLE_SIGNING_IDENTITY` | Full Developer ID Application identity |
| Variable | `APPLE_TEAM_ID` | Apple developer team ID |
| Secret | `APPLE_ID` | Apple account for notarization |
| Secret | `APPLE_PASSWORD` | App-specific Apple password |
| Variable | `OAW_REQUIRE_MARKETPLACE` | `true` to require the deployed official Marketplace for pushed tags only; unset/false permits Store-unavailable releases |
| Variable | `OAW_SIGNED_RELEASE` | `true` to require platform certificates and notarization (independent of updater keys) |

Certificate acquisition and account verification happen outside this repository. The PFX path supports exportable certificates; hardware-backed or cloud signing needs the provider's signing integration. Signing also does not guarantee immediate Windows SmartScreen reputation. Do not set `OAW_SIGNED_RELEASE=true` until the credentials are installed; the workflow deliberately fails rather than silently publishing unsigned builds under this setting.

Without credentials, manual and tagged builds keep the existing unsigned Windows and ad-hoc macOS behavior. The native menu explains that automatic updates are unavailable and offers the official releases page. `createUpdaterArtifacts` and the public key are injected only into configured release builds. Private keys are never written to app configuration or payloads.

Updater signing keys are separate from the Windows/Apple publisher certificates. The updater keypair can be generated locally, but its private key needs secure long-term retention before enabling the stable update channel. A test key is not a production signing identity.

When updater keys are configured, all three build jobs upload signed updater artifacts (`.exe` for Windows; `.app.tar.gz` for macOS). The release job assembles `latest.json` only when every platform and version agrees, each payload and signature file is present, and the manifest signatures and URLs match those assets. Publish that manifest and its matching artifacts together. The fixed endpoint uses GitHub's latest stable release; prereleases are installed manually and do not advance this channel. Keep the same signing key across releases. Older builds without the updater need a one-time manual upgrade.

### Update backup and recovery

After the user confirms installation, OAW hides its workspace, requests a clean backend shutdown, and invokes the bundled backup command. A shutdown timeout, active store lock, failed copy, changed source, or failed integrity verification cancels installation. The entire active managed data directory is copied to a sibling `*.before-update-<time>-<id>` folder; SQLite data, documents, installed packs and encryption keys remain together. A pending storage move is not executed during backup. External folders outside the managed data directory and unsaved UI drafts are not included: finish active work and save drafts before confirming.

The native dialog shows the retained backup path. Backups are never automatically deleted. To restore, quit all OAW processes, retain the current failed data directory for investigation, and restore the backup **at its original source path** recorded in `.oaw-update-backup.json`; do not merge two versions' files. Use the matching earlier app installer if reverting a data migration. Keep backups private because they include credential storage.

### Code review and preview validation

The **Desktop native checks** PR workflow runs Rust formatting and native tests on Windows x64 and both macOS architectures without signing credentials. Its placeholder resource directory does not produce an installable app or validate an upgrade. Backend tests cover backup rejection and recovery, disabled/signed configuration transitions, and incomplete or inconsistent update assets.

Run **Desktop release** manually on the PR branch to validate the real payload and installers. This does not create or publish a GitHub Release. Payload self-tests exercise the installed backup command and restore synthetic documents, a database and encrypted model credentials, including Windows DPAPI. macOS also runs this test from the bundled `.app` resource layout. These checks do not exercise native update dialogs or replace installing the app on a clean machine.

### Native acceptance before publication

Before a signed public release:

- Configure the Windows signing certificate and Apple Developer ID/notarization credentials listed above. Configure and securely retain the updater keypair only if automatic updates are wanted.
- The official Marketplace origin is configured in `backend/official_marketplace.py`. Set the repository variable `OAW_REQUIRE_MARKETPLACE=true` for public releases that promise Store access. The gate checks the embedded HTTPS origin on pushed tags only; it does not probe service health. Before publication, repeat [official acceptance](pack-store.md#acceptance) with no endpoint override against the actual release payload and complete installed-app acceptance. Manual preview, signing and updater policies remain independent.
- Build real signed installers, verify platform signatures/notarization, and complete the installed-app acceptance below.
- Verify the stable manifest and asset URLs against the release assets before publishing them together.

On Windows x64 and both macOS architectures, test a real installed version upgrading to a newer signed draft/release: check/download, cancel, corrupt-signature rejection, install/relaunch, saved model credentials and workspace, offline check, backup failure/disk full, and pending storage migration. Verify Authenticode on Windows and Gatekeeper/stapling on macOS. CI's signature checks do not replace these tests. Check each promised local Sandbox runtime separately using its [platform prerequisites](sandbox-workspace.md).

GitHub Release downloads remain the public distribution channel. Actions artifacts are temporary build/test outputs.
