# Make and share your first Pack in 10 minutes

**Goal:** create a card with one saved setting, package it as a `.oawpack` file and
give it to someone else. No Python code and no frontend build are needed for this
path. When you later want a custom interface, continue with
[declarative cards](declarative-packs.md).

## Before you start

You need Python 3.11 or newer and a checkout of OAW (the Pack tool currently ships
inside it). Define two shell variables once:

```sh
git clone https://github.com/theAfish/open-agent-world.git
export OAW_PYTHON=python3                              # or the interpreter you run OAW with
export OAW_PACK="$PWD/open-agent-world/scripts/oaw-pack.py"
```

## 1. Create the project

```sh
"$OAW_PYTHON" "$OAW_PACK" init ./my-card
cd my-card
```

`init` copies the Notes template with a new identity such as
`local.my-card-<random suffix>`. Keep this ID unchanged for every later release.

## 2. Make it a configuration-only card

The template also contains a React view. For your first Pack, remove it:

1. Delete the `frontend/` folder, `build.mjs` and `package.json`.
2. In `oaw.pack.toml`, delete the whole `[components.frontend]` table and the
   `frontend = { body = "note" }` line.

The card now uses the host's standard configuration editor.

## 3. Describe your setting

Edit `config.schema.json`. Every property needs a default:

```json
{
  "type": "object",
  "additionalProperties": false,
  "properties": {
    "message": { "type": "string", "default": "Hello", "maxLength": 2000 }
  }
}
```

Edit `[[contributions.cards]]` in `oaw.pack.toml` to set the card's `description`,
`icon` and `color`.

## 4. Check, package, inspect

```sh
"$OAW_PYTHON" "$OAW_PACK" doctor .
"$OAW_PYTHON" "$OAW_PACK" build-project . dist/my-card-0.1.0.oawpack
"$OAW_PYTHON" "$OAW_PACK" inspect dist/my-card-0.1.0.oawpack
```

`doctor` reads your declaration without running code. `build-project` writes a new
file each time, so use a new output path or raise `version` in `pyproject.toml`.
`inspect` prints the manifest the recipient will see.

Optionally run `check . --lifecycle --card <card id>` to activate the card in a
temporary profile; it needs OAW's backend dependencies installed.

## 5. Try it in a clean profile

Install the file in a separate data directory, not your working profile:

```sh
"$OAW_PYTHON" "$OAW_PACK" install dist/my-card-0.1.0.oawpack --data-root /path/to/test-profile
```

Start OAW with `OPEN_AGENT_WORLD_DATA_ROOT=/path/to/test-profile`, open
**Pack & Card Library → Packs**, find your Pack, add the card to a deck and place it.
A new configuration-only Pack activates without a restart when installed through
the Library (**Install Pack from File…**).

## 6. Share it

Send the `.oawpack` file, or attach it to a release in your own repository. Tell
recipients:

- the OAW version it needs (shown by `inspect` under `compatibility`),
- any Packs they must install first (under `dependencies`),
- that Packs run as trusted code, so they should only install files from sources
  they trust.

There is no online Store yet, no publisher verification, and dependencies are not
installed automatically. See [Pack installation and distribution](../pack-distribution.md).

## Next steps

- Add a custom interface: [declarative cards](declarative-packs.md).
- Share a configured team of Agents instead of a card: [content Packs](../creator-packs.md).
- Design card faces without code: [Card Factory](../card-factory.md).
