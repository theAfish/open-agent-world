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
| Task Board | Track tasks and dependencies with connected Agents |
| Skill Toolboxes | Give Agents reusable skills and instructions |
| Agent Barracks | Prepare Agents with equipment and summon them when needed |
| Structure Viewer | View supported structure files opened in connected cards |
| MatCreator | Work with a materials research team and task workflow |
| SQLite | Keep a database card and grant scoped database operations |
| Codex | Use an alternative Agent runtime, when its requirements are installed |

## Install a local Pack

In **Pack & Card Library → Packs**, choose **Install Pack from File...** and select
a `.oawpack`. Review its name and version, then choose **Install Pack**. Packs run
trusted application code: use files from a source you trust.

Restart OAW when prompted. The Pack remains **Installed** while its Sandbox
runtime is preparing or if preparation fails. Use **Retry environment preparation**
under **Manage installed Packs** after addressing the reported conflict or
connectivity problem. Open the Pack once it is ready and add its cards to your Deck.

Install a newer version from file to upgrade. Select a retained version with
**Use this version on restart** to roll back. The Store tab remains a placeholder;
remote discovery, download and publishing are not available yet.

## Disable or remove a Pack

Remove its cards and dependent objects from the World first; the Library explains
remaining usages. Collection and Deck references survive disable and uninstall.
Third-party Packs expose **Uninstall on restart** in local Pack management.
An inactive version can be removed after it is neither selected nor loaded.
Core functionality stays enabled; bundled Packs do not use the external installer.

To create your own, see [Local Pack distribution](../pack-distribution.md).
