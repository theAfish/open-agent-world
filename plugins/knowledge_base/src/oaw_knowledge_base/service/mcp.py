"""MCP over stdio: agent operations as tools for any harness that speaks MCP.

Tool names, descriptions and JSON Schemas come straight from
:mod:`oaw_knowledge_base.operations`, so a harness calling ``knowledge_markdown``
through MCP gets exactly what an OAW Agent gets calling the capability of the same
name. Raw-byte upload, settings and human publication stay absent. Projects,
Space definitions, conversion and optional result review share the card contract.
"""
from __future__ import annotations

import json

from ..errors import KnowledgeError
from ..operations import AGENT_OPERATIONS

INSTALL_HINT = ("The MCP server needs the `mcp` package: "
                "`pip install \"oaw-knowledge-base[service]\"`.")


def tools_for(module):
    """The agent operations as MCP ``Tool`` objects, in table order."""
    return [module.Tool(name=operation.tool_name, description=operation.description,
                        inputSchema=operation.input_schema())
            for operation in AGENT_OPERATIONS]


def call(store, tool_name, arguments, *, actor="mcp"):
    """Run one tool call and return its JSON text, or a message the model can act on."""
    operation = next((item for item in AGENT_OPERATIONS if item.tool_name == tool_name), None)
    if operation is None:
        raise KnowledgeError(f"No such tool: {tool_name}")
    # Desktop handlers may accept a wider contract than the agent schema. Validate
    # here as well as advertising it, so a raw MCP caller cannot reach those paths.
    validated = operation.model.model_validate(arguments or {}).model_dump(exclude_unset=True)
    result = store.run(operation.name, validated, actor=actor)
    return json.dumps(result, ensure_ascii=False, default=str)


def build_server(store):
    try:
        import mcp.types as types
        from mcp.server.lowlevel import Server
    except ImportError as error:  # pragma: no cover - depends on the deployment env
        raise KnowledgeError(INSTALL_HINT) from error

    server = Server("oaw-knowledge-base")

    @server.list_tools()
    async def list_tools():
        return tools_for(types)

    @server.call_tool()
    async def call_tool(name, arguments):
        # The handler is synchronous and holds the store's mutation lock, so hand it to
        # a worker thread rather than blocking the MCP event loop.
        from anyio import to_thread

        text = await to_thread.run_sync(
            lambda: call(store, name, arguments))
        return [types.TextContent(type="text", text=text)]

    return server, types


def serve_stdio(store):
    import anyio

    server, _ = build_server(store)

    async def run():
        from mcp.server.stdio import stdio_server

        async with stdio_server() as (read_stream, write_stream):
            await server.run(read_stream, write_stream, server.create_initialization_options())

    anyio.run(run)
