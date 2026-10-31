"""Time & Count Pack, using host documents and connection-derived capabilities."""
from open_agent_world.plugin_api import (
    CapabilityDefinition, CapabilityGrantDefinition, NodeDocumentAction, NodeDocumentDefinition,
    NodePresentation, NodeTypeDefinition, PackDefinition, PluginDescriptor, RelationshipDefinition,
    ScopedStateSpec,
)
from .engine import EmptyConfig, OPERATIONS, PomodoroDocument, TimeDocument, handler
from .icons import assets

PREFIX = "tools.time"
CARDS = {
    "digital": ("Digital clock", "clock", (416, 156), ("read", "set_time", "set_zone", "reset")),
    "clock": ("Analog clock", "clock", (260, 260), ("read", "set_time", "set_zone", "reset")),
    "calendar": ("Calendar", "calendar", (336, 368), ("read", "set_date", "set_zone", "reset")),
    "countdown": ("Countdown", "timer", (264, 148), ("read", "set_duration", "start", "pause", "reset")),
    "stopwatch": ("Stopwatch", "timer", (264, 164), ("read", "start", "pause", "lap", "reset")),
    "pomodoro": ("Pomodoro", "timer", (224, 224), ("read", "set_duration", "start", "pause", "reset")),
    "counter": ("Counter", "hash", (256, 132), ("read", "increment", "decrement", "set_count", "reset")),
}


class TimePlugin:
    descriptor = PluginDescriptor(id=PREFIX, version="0.1.0", plugin_api_version="1.41",
        name="Time & Count", description="Seven modern timepieces, timers, a calendar and a counter.")

    def register(self, registration):
        for asset in assets():
            registration.register_asset(asset)
        for operation, (model, label, description) in OPERATIONS.items():
            kind = f"{PREFIX}.{operation}"

            async def invoke(context, capability, arguments, operation=operation):
                if operation == "read":
                    return (await context.node_document_action(capability, operation, arguments))["value"]
                # The narrow context performs the host's live permission and revision checks.
                # Read uses a separate capability, granted alongside every mutation below.
                read_capability = capability.model_copy(update={"id": f"{PREFIX}.read:{capability.target_id}", "kind": f"{PREFIX}.read"})
                current = await context.node_document_action(read_capability, "read", {})
                return (await context.node_document_action(capability, operation, arguments, current["revision"]))["value"]

            registration.register_capability(CapabilityDefinition(kind=kind, tool_name=f"time_{operation}",
                description=description, input_schema=model.model_json_schema()), invoke)
            targets = frozenset(f"{PREFIX}.{card}" for card, (_, _, _, operations) in CARDS.items() if operation in operations)
            grants = (CapabilityGrantDefinition(kind),) if operation == "read" else (
                CapabilityGrantDefinition(f"{PREFIX}.read"), CapabilityGrantDefinition(kind))
            registration.register_relationship(RelationshipDefinition(id=kind, label=label, short_label=label,
                description=description, source_traits=frozenset({"core.agent"}), target_types=targets,
                capabilities=grants, templateable=True))

        for card, (label, icon, size, operations) in CARDS.items():
            model = PomodoroDocument if card == "pomodoro" else TimeDocument
            registration.register_node_type(NodeTypeDefinition(id=f"{PREFIX}.{card}", label=label,
                description=f"{label} · Time & Count", icon=icon, icon_asset=f"time-{card}", color="#648fc3",
                deck_id="time", deck_label="Time & Count", deck_icon="clock", default_name=label,
                default_size=size, default_status="available", statuses=frozenset({"available"}),
                config_model=EmptyConfig, state=ScopedStateSpec(), templateable=True,
                traits=frozenset({"ui.content-surface.v1"} | ({"ui.ellipse.v1"} if card in {"clock", "pomodoro"} else set())),
                presentation=NodePresentation(states=("preview",), initial="preview", open="preview",
                    sizes={"preview": {"width": size[0], "height": size[1]}}),
                frontend={"preview": "timepiece"},
                document=NodeDocumentDefinition(model=model,
                    capture=lambda value: {**value, "started_at_ms": None, "elapsed_ms": 0, "laps": [], "updated_at_ms": 0},
                    actions={operation: NodeDocumentAction(handler(card, operation),
                        capability_kind=f"{PREFIX}.{operation}", read_only=operation == "read", project=operation == "read")
                        for operation in operations})))
        registration.register_pack(PackDefinition(id=f"{PREFIX}.default", name="Time & Count",
            description="Digital and analog clocks, calendar, countdown, stopwatch, Pomodoro and counter.",
            cards=tuple(registration.nodes), accent_color="#648fc3"))


def create_plugin():
    return TimePlugin()
