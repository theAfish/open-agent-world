"""Keep MatCreator assets usable while explicitly blocking its local worker.

The existing native worker inherits host environment and starts a host process.
It must not receive this deployment's cloud management credentials. A future
adapter must carry its trusted tool bridge across the remote execution boundary.
"""


def hosted_matcreator(plugin):
    from backend.agents import AgentDependencyError
    from oaw_matcreator.native_runtime import MatCreatorRuntime

    class PendingRemoteMatCreator(MatCreatorRuntime):
        async def execute(self, config, context, runtime_input):
            raise AgentDependencyError(
                "MatCreator native execution is not adapted to the managed cloud environment yet. "
                "Its knowledge assets and database remain available; use the standard OAW Agent for the Sandbox POC."
            )
            if False:
                yield

    class HostedMatCreator:
        descriptor = plugin.descriptor

        def register(self, registration):
            plugin.register(registration)
            registration.runtime_provider_factories["matcreator.native"] = PendingRemoteMatCreator

    return HostedMatCreator()
