# Publishing desktop releases

The **Desktop release** workflow builds Windows x64, macOS Apple Silicon and macOS Intel from the same commit. macOS packaging was integrated from `codex/macos-desktop-preview`; a separate branch is no longer needed for these builds.

## First release

1. Commit the packaging/workflow/docs changes and merge them into `main` so the download instructions appear on the repository homepage and the manual workflow is discoverable.
2. In **Actions → Desktop release → Run workflow**, select the release branch. This builds all three installers and uploads Actions artifacts without creating a Release.
3. Download the artifacts and test installation, first launch, model configuration and upgrade on the corresponding operating systems. Payload self-tests do not replace installing the app on a clean machine.
4. Ensure the version agrees in `desktop/package.json`, `desktop/package-lock.json`, `desktop/src-tauri/tauri.conf.json`, `desktop/src-tauri/Cargo.toml` and the application entry in `desktop/src-tauri/Cargo.lock`. Update `docs/release-notes.md` with the changes for this version.
5. Tag that commit, for example `git tag v0.1.0`, then `git push origin v0.1.0`. The tag must equal `v` plus the desktop version. All three builds must pass before a draft Release is created with installers and individual SHA-256 checksums.
6. Open **Releases**, edit the draft, check assets and notes, and click **Publish release**. Use **Set as a pre-release** for experimental builds. The README links to `/releases`, so preview releases are discoverable too.

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

The updater can be reviewed and merged while disabled by default. Ready for review means the code and preview build checks are complete; it does not mean the stable update channel is ready to enable. Keep the publication requirements below explicit rather than treating unavailable production credentials as a code-review prerequisite.

### Native acceptance before publication

Before a signed public release:

- Configure the Windows signing certificate and Apple Developer ID/notarization credentials listed above. Configure and securely retain the updater keypair only if automatic updates are wanted.
- If Marketplace support is required, deploy and smoke-test it, bind its origin in `backend/official_marketplace.py`, and set `OAW_REQUIRE_MARKETPLACE=true`. Otherwise leave the constant as `None`; installers remain releasable with Store unavailable.
- Build real signed installers, verify platform signatures/notarization, and complete the installed-app acceptance below.
- Verify the stable manifest and asset URLs against the release assets before publishing them together.

On Windows x64 and both macOS architectures, test a real installed version upgrading to a newer signed draft/release: check/download, cancel, corrupt-signature rejection, install/relaunch, saved model credentials and workspace, offline check, backup failure/disk full, and pending storage migration. Verify Authenticode on Windows and Gatekeeper/stapling on macOS. CI's signature checks do not replace these tests. macOS local Sandbox support remains a separate limitation.

GitHub Release downloads remain the public distribution channel. Actions artifacts are temporary build/test outputs.
