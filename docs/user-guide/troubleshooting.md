# Troubleshooting

## Start with Help

Open **Help (?) → Status check** in the canvas controls. The check covers saved
cards across the whole canvas, including off-screen cards. It checks backend and
live-update connections, card/plugin status, configured model credentials, and
Sandbox runtime availability. Cards inside groups or backpacks can be located
through their outer container or owner.

Results distinguish **Needs attention**, **Note / unverified**, and **Check
passed**. Follow the suggested settings, Library or repair-guide link, then use
**Check again**. A stopped Sandbox is not an error. Plugin Agents with their own
runtime do not require OAW model credentials.

Checks do not send model requests, execute tools, start Sandboxes or repair data.
An available configuration does not prove that an external service will respond.
Plugins without a host check remain unverified; inspect their own card status.

**Help → Documentation** opens the documentation site. **Help → Versions and
updates** opens official releases and installation instructions. This build does
not have an automatic installer; close OAW before installing a desktop update.
Source installations and hosted deployments are updated by their operator.

## The tray is empty

Open **Pack & Card Library**, open a pack, add collected cards to a deck, and activate that deck. A fresh installation can have unopened packs and an empty deck.

## An Agent cannot respond

Open **Settings → Models** and confirm that the connection is enabled and has a valid model and credentials. Check the Agent's selected model. Read the error in its activity or Conversation; a rejected key, unavailable model, and unreachable service need different corrections.

If you are running a development build with the mock runtime, it produces test responses instead of real model answers.

## An Agent cannot read a document or use a tool

Check the connection and its selected relationship. Merely placing cards together does not grant access. A read-only connection does not permit edits. Reconnect with the access you intend to grant.

## A card is unavailable

Check that its plugin is enabled and its pack has been opened. Add the collected card to your active deck. Some plugins need additional software or a compatible app build; follow their installation instructions.

## A Sandbox cannot start

Read the runtime diagnostics in the Sandbox card and address the reported missing prerequisite. Creating a Sandbox card does not start its environment. macOS currently has no local Sandbox runtime; ordinary Agent conversations and direct document access can still be used.

## The tutorial gets in the way

Minimize its bubble, pause it, or skip with ×. Use **Help (?) → Tutorial** to replay it. The tutorial uses real cards; your placed cards remain in the world afterward.

## Report a problem

[Open an issue](https://github.com/theAfish/open-agent-world/issues) with your OS, OAW version, the steps you tried, the expected result, and the visible error. A screenshot can help. Remove API keys and private document content before sharing logs or images.
