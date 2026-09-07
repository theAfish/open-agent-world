# Execution configuration cards

Environment Profiles and Compute Targets are ordinary registered nodes with
editable, revisioned node documents. Connect them to an Agent, or equip them
using the existing equipment controls. Access grants permission to select a
resource for explicit invocation; this Agent connection never injects configuration automatically. A separate `environment.default` connection from a Profile to a Sandbox establishes its live shared default, as described in [Sandbox configuration](sandbox-workspace.md).

An Environment Profile (`environment`) contains a `variables` object. Each value
is either an ordinary string or an object containing only `secret_ref`:

```json
{"variables":{"DEMO_REGION":"local-test","API_TOKEN":{"secret_ref":"service-token"}}}
```

Save the document, then bind each secret in the card's private credential section.
The host-only `GET /api/nodes/{id}/credentials` returns configured booleans;
`PUT /api/nodes/{id}/credentials/{reference}` takes `value` and the document's
`expected_revision`. A null value unbinds it. These endpoints are part of the
existing trusted local UI API, and are not Agent tools. There is no secret-read
HTTP endpoint. Do not put secrets in ordinary string fields.

The separate `environment.use` capability exposes `inspect_environment_profile`
and permits explicit execution selection. Inspection returns references only.
Bindings use the existing encrypted settings facility and account-protected key
(Windows DPAPI; protected key file on other hosts). They are indexed by node ID,
creation timestamp, and reference in host-private application settings. Neither
documents, node configuration, equipment nor portable templates contain bindings.
Copies, summons, template instances, and recreation of a deleted ID require
explicit rebinding. Back up the private host data directory as sensitive data;
portable graph capture is a different operation from a host backup.

A Compute Target (`compute-target`) contains `name`, `provider_id`, and an object
`config`. All fields are public, non-sensitive configuration. `provider_id` is
descriptive; no provider connection, scheduler or remote execution is implied.
The `compute_target.read` connection exposes one `read_compute_target(target)`
operation. Keep authentication in an Environment Profile.

## Tool and dispatch contracts (plugin API 1.11)

`execute_command(sandbox, argv, environment?, target?)` and
`run_skill_script(sandbox, skill, script, argv?, interpreter?, environment?, target?)`
retain one operation each. Arguments accept current aliases, unambiguous exact
names, or node IDs. Omit optional selectors to use the Sandbox's linked default and local overrides. Null,
arrays, ambiguous names and unauthorized selections fail. An explicit invocation profile replaces the linked layer; local overrides still apply. Arbitrary multi-profile inheritance is unsupported. Agents without either configuration grant retain their
otherwise authorized execution tools.

`CapabilitySelector.required=False` is the generic optional-resource contract.
Required selectors still gate tool exposure; optional ones do not. Optional
selectors with no current grants are omitted from the advertised fields, avoiding
unusable empty enum schemas. Every selected resource is resolved against the
live graph. Immediately before dispatch, the
service rechecks Sandbox execution, Skill access when applicable, and every
selected configuration grant under the graph mutation lock. It then reads the
current documents and resolves the selected profile's secrets. No resolved values
are stored on a card, runtime, or reusable command object.

Sandbox backends receive the validated command-only `invocation_env` mapping,
alongside the existing argv/runtime-mount contract. The original `env` parameter
retains its narrow backend allowlist. An implementation must inject the new
mapping only into the isolated command and its children, or explicitly reject
it. Backends explicitly opt in with `supports_invocation_environment=True`; the
default rejects configuration selection. It must never ignore unsupported configuration. Existing implementations
use the same validation in Windows AppContainer, native Linux, and WSL2.

The single target carrier is `OAW_TARGET_CONFIG_JSON`: a JSON serialization of
the complete target document, including `name`, `provider_id`, and structured
`config`. No fields are flattened into variables or interpreted by the host.

Plugins can specialize the public `ComputeTarget` Pydantic model, override
`config` with their own validated model, and register it through
`NodeDocumentDefinition.model` on a node with the `core.compute-target` trait.
Reuse the core read document action (or declare a read-only action associated
with `compute_target.read`). The existing relationship, selector, equipment and
document validation contracts then apply without a provider registry. For example:

```python
from pydantic import BaseModel, ConfigDict, Field
from open_agent_world.plugin_api import ComputeTarget

class DestinationFields(BaseModel):
    model_config = ConfigDict(extra="forbid")
    replicas: int = Field(default=1, ge=1, le=4)

class Destination(ComputeTarget):
    config: DestinationFields = Field(default_factory=DestinationFields)
```

## Environment and security boundaries

Names must be portable identifiers and unique ignoring case. Values must be
NUL-free strings. The combined invocation environment is limited to 24 KB of
UTF-8 bytes; larger configurations fail explicitly. A profile cannot set the
target carrier or any `OAW_`/`SANDBOX_` variable. Loader variables such as `LD_*`
and `DYLD_*`, startup variables such as `BASH_ENV`, `ENV`, `PYTHONPATH`,
`NODE_OPTIONS`, and host-controlled paths, homes, temp directories, Windows
startup settings and WSL interop settings are reserved. The authoritative list
is in `backend/sandbox/environment.py`; unsafe overrides are rejected, including
case variants. Conflicts with backend `env` overrides also fail. Safe locale and
encoding controls retain their existing support.

Windows constructs an explicit AppContainer process environment. Linux applies
variables using bubblewrap's `--setenv` after `--clearenv`; systemd/bubblewrap
launchers retain their separate minimal host environment. WSL carries structured
configuration through its trusted stdin protocol and applies the same Linux
validation; the Windows transport and Python worker never inherit these values
as their own environment. Transport payloads and launcher arguments are visible
to trusted host administrators. This does not protect secrets from the host.

No backend enables networking because a target or profile is selected. Existing
filesystem, network, process-tree and resource-limit protections continue to
apply. Target configuration does not grant remote connectivity.

Known secret values are redacted from surfaced results, error messages and
Sandbox events. For commands with secrets, raw stdout/stderr events are withheld
until the bounded result is complete, preventing split-chunk disclosure; redacted
output is then published. Other commands retain streaming behavior. Transformed,
encoded, truncated or externally written secrets are not reliably recognizable.
Trusted plugin/native diagnostic logging must likewise avoid logging request
payloads. Code receiving a secret can read it and write it into any resource it
can access. Redaction is not authorization, isolation, or data-loss prevention.

See `examples/skills/execution-configuration` for a local-only example.

## Validation

`backend/tests/test_execution_config.py` covers omission, ordinary/equipped
selection, revocation before dispatch, cross-invocation isolation, secret
encryption/inspection/restoration, reserved variables, provider-specific document
validation, output redaction, and backend carrier contracts. Its real OS test is
opt-in with `OAW_TEST_SANDBOX_RUNTIME=windows`, `linux`, or an existing
`wsl:<distribution>` and requires a supported, available isolation runtime.
Unit tests with a fake native API do not prove native isolation. No runtime,
distribution, interpreter, package or network dependency is installed by this
feature.
