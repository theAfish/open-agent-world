# Packs and cards

Packs add cards and tools to your world. Open a Pack to collect its cards, choose
which belong in your Deck, then place them in your World.

## Use an installed Pack

1. Open **Pack & Card Library**.
2. Find and open a Pack.
3. Add the collected cards you want to your active deck.
4. Place a card from the bottom tray.
5. Connect it to the Agent or resource that should use it, then choose a supported relationship.

Installing, collecting, and placing are separate steps. Installing a Pack does not automatically fill your tray.

## Choose a tool

Availability depends on your installed build and the Pack's requirements.

| Pack | What you can do |
| --- | --- |
| Core essentials | Agents, resources and workspaces, plus [Task Board](../task-board.md), [Skill Toolboxes](../skill-toolboxes.md) and [Agent Barracks](../agent-barracks.md) |
| Structure Viewer | View supported structure files opened in connected cards |
| MatCreator | Work with a materials research team and task workflow |
| SQLite | Keep a database card and grant scoped database operations |
| Codex (optional install) | Use a local Codex runtime after installing its Pack and configuring Codex |

## Install a local Pack

In **Pack & Card Library → Packs**, choose **Install Pack from File...** and select
a `.oawpack`. Review its name, version and installation plan, then choose **Install Pack**. Packs run
trusted application code: use files from a source you trust.

Restart OAW when prompted. In **Pack details**, application installation and
runtime environments have separate status: a Pack can be loaded while a Sandbox
environment is **Prepared when used**, or a particular feature still needs
configuration. An optional environment does not make every card unavailable.
For a failed legacy shared environment, use **Retry environment preparation** in
the Pack's details after addressing the reported conflict or connectivity problem.
Open the Pack and add its cards to your Deck.

Installation progress remains visible when you return to the Library. If a request
disconnects, check the existing operation before retrying; a timeout does not mean
the backend stopped installing. You do not need to run npm or pip manually for
an archive that declares supported managed environments.

Install a newer version from file to upgrade. Select a retained version with
**Use this version on restart** to roll back.

## Get a Pack from the Store

Open **Pack & Card Library → Store**, search or browse, and open a Pack's details
to see its compatibility, dependency plan and environment requirements. **Get**
or **Update** on a catalog tile opens these details first. Choose the action in
the details to install, then restart OAW when
prompted. The downloaded Pack appears in the same **Packs** tab as local Packs.
Open it, add its cards to your Deck and place them in your World.

**Update available** offers an explicit **Update** action; OAW does not update
Packs automatically. If the Store is unavailable, use **Retry** later. Your
existing Packs, Cards, Deck and World keep working. OAW connects to the official
Marketplace at `https://open-agent-world.space` automatically. Install required dependency Packs first if the installation plan reports them.

## Disable or remove a Pack

Remove its cards and dependent objects from the World first; the Library explains
remaining usages. Collection and Deck references survive disable and uninstall.
Choose **Uninstall Pack** in Pack details to review blockers and dependency cleanup.
The plan distinguishes resources removed now, retained for other Packs, and
released after restart. Source Packs keep their source files and offer
**Restore Pack** after removal. Unused managed dependency environments are
released after the last Pack using them has unloaded; external resources and
user data are retained.
An inactive version can be removed after it is neither selected nor loaded.
Core functionality stays enabled. Source, bundled and downloaded Packs share
the same enrollment and cleanup checks.

Create cards without code in [Card Factory](../card-factory.md), or build a
Python/React Card App with the [developer guide](../developers/index.md).
