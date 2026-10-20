"""Trusted MatCreator orchestration inside a dedicated AgentCore profile.

The maintained engine exposes typed orchestration and OAW capability tools;
generated commands go through the host's remote-only Sandbox registry. This
worker is trusted application code, not an OS isolation boundary. Never load
user-provided Python plugins into this deployment.
"""
import os


def hosted_matcreator(plugin):
    from oaw_matcreator.native_runtime import MatCreatorRuntime

    class HostedMatCreatorRuntime(MatCreatorRuntime):
        def _worker_environment(self):
            # Allow only process/platform essentials. In particular no cloud
            # keys, OIDC secrets, model credentials, proxy settings, Python
            # import overrides or arbitrary future provider variable names.
            names = {"PATH", "HOME", "USER", "LOGNAME", "LANG", "LC_ALL", "TZ",
                     "TMPDIR", "TMP", "TEMP", "SYSTEMROOT", "WINDIR"}
            env = {key: value for key, value in os.environ.items() if key.upper() in names}
            env.update(PYTHONUTF8="1", PYTHONUNBUFFERED="1")
            return env

    class HostedMatCreator:
        descriptor = plugin.descriptor

        def register(self, registration):
            plugin.register(registration)
            registration.runtime_provider_factories["matcreator.native"] = HostedMatCreatorRuntime
            # Keep personal defaults separate; cloud commands use authenticated
            # VM access and public egress controlled by the managed firewall.
            definition = registration.legion_presets["matcreator.native-research"]
            registration.legion_presets[definition.id] = definition.model_copy(update={
                "revision": definition.revision + 2,
                "nodes": tuple(node.model_copy(update={"config": {
                    **node.config, "network_enabled": True, "command_timeout": 60,
                }})
                               if node.type == "sandbox" else node for node in definition.nodes),
            })

    return HostedMatCreator()
