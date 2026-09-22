# Models and settings

An Agent needs a model connection to respond. A connection tells OAW which service to use and how to sign in; a model selects the specific model offered by that service.

## Connect a model

1. Open the gear button, then **Models**.
2. Add a connection and choose your provider or a compatible endpoint.
3. Enter the service address if required and your API key. Local services may not need a key.
4. Add a model using the exact model ID from your provider. Give it a friendly display name.
5. Choose a default model and save.

Open your Agent's settings and select the model. Existing Agents can keep their own model selection, so changing the default does not necessarily change every Agent.

Model lists are entered manually. If a model does not appear, add it to an enabled connection first.

## Update credentials

Enter a new key to replace the saved one. Leaving the field blank keeps the saved key. Use **Remove saved key** to delete it explicitly.

Changes apply to subsequent requests. An already running request keeps the connection it started with.

## Keep your work

Your app data is separate from the installer. To move it, use **Settings → Storage** and select a new or empty folder. Save, close OAW, and start it again to complete the move. Allow enough free space for a copy; the original folder is retained as a backup.

Sandbox working folders have their own settings. If you only want to change where a Sandbox works, use **Settings → Sandbox** or that Sandbox's settings.

## If it does not connect

Check that the connection is enabled, the address is correct, and the model ID matches your service. Read the displayed error before retrying. See [Troubleshooting](troubleshooting.md).
