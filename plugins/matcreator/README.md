# MatCreator on OAW

Use a materials-research Agent with a Conversation, Sandbox, Structure Viewer,
knowledge tools and tracked jobs. The **MatCreator research** formation provides
connected cards and a saved workspace layout.

## Install and start

Install the MatCreator Pack and its required Packs through the Library. In a
source checkout use [setup](../../docs/developers/setup.md) with the intended
profile and `--plugin-path plugins/matcreator`. The Pack declares its private
Python worker environment in [oaw.pack.toml](oaw.pack.toml); it is separate from
the host interpreter and Sandbox environments.

Open MatCreator in the Library, collect its cards and place **MatCreator research**.
Choose a model for its Agent. Select and start a usable Sandbox before asking for
execution. Open Conversation and send a small, bounded task; inspect tool activity
and produced files before trusting the result.

See the [Chinese workspace walkthrough](../../docs/user-guide/matcreator-example.zh-CN.md)
for the visible layout and controls. Existing formations retain their saved layout;
adding a newly available card does not rewrite an existing formation.

## Follow work

**Agent graph** shows execution history. **Jobs → Agent** selects tracked jobs in
the current session: **Observe jobs** reads them; **Manage jobs** additionally
allows provider-supported controls. Pausing monitoring leaves external work
running; cancellation is confirmed only when the provider reports it.

Grouping cards grants no tool or data access. Agents use their live connections;
scientific results and model-generated knowledge still require review.

## Migrate standalone MatCreator data

Use the Pack-owned [`oaw_matcreator/migrate.py`](oaw_matcreator/migrate.py).
The CLI and [offline importer](oaw_matcreator/migration.py) ship in the Pack's
Python wheel. The package declares the `oaw-matcreator-migrate` console command;
in an installed OAW host environment, `python -m oaw_matcreator.migrate` is also
available. Source-checkout examples below run from the OAW repository root.
Stop the old Agent/server while reading its data, and stop the destination OAW
backend before importing. Closing a browser tab does not stop the backend;
for source development, press Ctrl+C in the terminal running `scripts/dev.ps1`
and wait for it to exit. Install and enable the MatCreator Pack in the
destination first. `--data-root` is the actual OAW data directory shown in
Settings, including any configured storage relocation.

Run `local` and `import` from this checkout with the backend Python environment
(Windows: `backend/.venv/Scripts/python.exe`; Linux/macOS: `backend/.venv/bin/python`).
The `export` and `inspect` commands need only Python 3.11+; the script can be
copied to the old computer without installing OAW or MatCreator dependencies.

Local import keeps project files, the complete history database and execution
graph files in their original locations. OAW imports visible conversation text
and small knowledge/Skill metadata, and saves a small history-reference index
for each session. It does not copy the original database, raw tool histories or
large project files, and does not make a full backup copy of the destination
database. Record changes are protected by one transaction.

```sh
python plugins/matcreator/oaw_matcreator/migrate.py local --source /path/to/.matcreator --data-root /path/to/oaw-data --dry-run
python plugins/matcreator/oaw_matcreator/migrate.py local --source /path/to/.matcreator --data-root /path/to/oaw-data
```

For another computer or server, export a portable ZIP64 package, copy it, then
import into a new project directory. Packaging streams files without compression
by default; add `--compress` when compression is useful.

```sh
python plugins/matcreator/oaw_matcreator/migrate.py export --source /path/to/.matcreator --output /transfer/research.zip
python plugins/matcreator/oaw_matcreator/migrate.py inspect --package /transfer/research.zip
python plugins/matcreator/oaw_matcreator/migrate.py import --package /transfer/research.zip --files-to /projects/imported-research --data-root /path/to/oaw-data
```

The default project directory is `SOURCE/workspace`; use `--workspace` for an
existing project elsewhere. Legacy `agents/MatCreator/.adk` layouts are detected.
For server installations, select one user's `.matcreator` directory; a session
database containing several owners requires `--user-id`. Repeat `--session ID`
to select histories. Shared knowledge and project files are still included.
Use repeatable `--include 'project-a/*'` / `--exclude '*.tmp'` globs when exporting
selected files, and `--knowledge-db` for an explicitly configured KDG database.

Project and user-global collected Skills are detected. Local import attaches
external Skill roots through separate read-only Sandboxes; portable export puts
their resources in `matcreator-imported-skills/` within the received workspace.
Use `--skills-dir` for additional roots. Resources larger than 1 MiB remain in
their Sandbox rather than being duplicated inside a knowledge document.
Caches, virtual environments, `.env` files and SSH directories are excluded from
packages. Application configuration and credentials are not exported; configure
model connections and compute environments on the destination.

Migration preserves the destination OAW's existing model settings, global
environment variables, private bindings and encryption key. It never replaces
them with source settings or credentials. The completion report checks inherited
environment bindings and lists variables that need configuration or rebinding.
Data import can succeed while execution setup still needs attention; repair
those variables in OAW before running research. Missing destination credentials
must be configured there explicitly.

By default import creates a new **MatCreator imported research** group. Use
`--group-id` for a fresh existing research group, `--runtime` to select the
destination Sandbox runtime, or `--read-only` to bind project files read-only.
Additional `--old-workspace` prefixes handle old container/mount paths, for
example `/root/.matcreator/workspace`; mapped messages and knowledge point to
the selected destination while source records retain their original paths.

After a successful import, the **Legions** deck contains a temporary reference
to the imported research, with the imported session and message counts. Click
it to open that existing Legion, including when it is outside the current
canvas view, or drag it onto the canvas to place the existing Legion and its
members at the drop position. Conversations, knowledge and resource connections
stay attached to the same backend records; subsequent drops move that same
Legion. It does not deploy a second copy. Remove the reference using the
deck's trash target when finished; conversations, knowledge and original files
remain. New research is placed beside existing canvas content, rather than
overlapping the origin. The command also prints a completion message, counts
and the destination deck. No separate migration-assistant card is installed.

Import adds Conversation history, supported knowledge relationships, memories,
and Skill resources. In local mode, complete tool histories remain in the
original database, referenced by small downloadable indexes. For portable
migration, complete tool events are streamed into bounded JSON parts outside
the manifest, so multi-gigabyte histories do not have to fit in memory or the
manifest size limit. Portable imports include those JSON parts as attachments.
Unsupported relationships
and inactive knowledge are preserved in a separate source attachment, with
warnings. Old Runs, approval state and native checkpoints are never activated;
choose a model before starting new research.

Imports hold the normal OAW profile lock. Records commit together; existing
work is preserved. Portable imports back up the destination SQLite database
under `backups/matcreator/`; local imports avoid this potentially large copy.
Space checks include metadata, copied history, project files and any database
backup, grouped by destination volume, with a 64 MiB free-space reserve.
Exports report an uncompressed size estimate and check space while streaming;
an incomplete package is removed on failure.
Repeated imports add new records and report conflicts instead of overwriting
edits. This also repairs older imports that omitted replies authored by
`MatCreator`: rerun the same local command with the destination backend stopped.
Missing replies are inserted in source order before destination-only messages,
preserving the existing Legion, session IDs and edited messages.
If the imported Legion and all of its recorded member cards have been
deleted, the same command creates a fresh import and replaces its stale deck
reference. Removing only the Legion container while keeping imported cards
requires restoring that group or deleting the remaining imported cards first.
No manual database cleanup or source-file deletion is needed.
Portable retries reuse only a directory recorded for that exact file
inventory; changed file selections require a separate destination profile.
If importing records fails after extraction, the complete received files remain
available and the same command can retry. A local import continues to depend on
the original project and Skill directories.

For WSL data, pass a `\\wsl.localhost\Ubuntu\home\user\.matcreator` source.
The tool reads lightweight metadata with Python inside that distribution and
maps source folders back to Windows paths. It creates no database snapshot or
intermediate project copy. Portable exports also run their SQLite reads inside
the source distribution. Source folders must remain available for local history
references and file bindings; portable imports are independent of them.

## Further reading

- [Runtime, dependencies, long computations and maintenance tests](docs/runtime.md).
- [Engine provenance](oaw_matcreator_engine/UPSTREAM.md).
- [Public execution-source API](../../docs/developers/execution-graph.md).

## Delegating research

See [native MatCreator execution](docs/runtime.md#native-matcreator-agent).
