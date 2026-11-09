"""Compose full Run tool archival with existing ADK authorization callbacks."""
from __future__ import annotations

import asyncio
import inspect


def archived_tool_callbacks(trace, role, callbacks=None):
    callbacks = dict(callbacks or {})
    if trace.invocations is None:
        return callbacks
    archive = trace.invocations
    identities = {}

    async def call(callback, **kwargs):
        if callback is None:
            return None
        result = callback(**kwargs)
        return await result if inspect.isawaitable(result) else result

    def context_id(context):
        # ADK assigns a function_call_id even for SDK delegation/finish tools.
        # A missing ID must not merge unrelated invocations.
        return getattr(context, "function_call_id", None)

    async def before_tool(tool, args, tool_context):
        call_id = context_id(tool_context)
        if not call_id:
            return await call(callbacks.get("before_tool_callback"), tool=tool, args=args, tool_context=tool_context)
        identity = trace.archive_tool_start(role, call_id, getattr(tool, "name", "tool"), args)
        identities[call_id] = identity
        try:
            result = await call(callbacks.get("before_tool_callback"), tool=tool, args=args, tool_context=tool_context)
            archive.tool_arguments(identity, args, overridden=result is not None)
            return result  # Never alter authorization/denials or execute the tool here.
        except (Exception, asyncio.CancelledError) as error:
            archive.error_tool(identity, error)
            raise

    async def after_tool(tool, args, tool_context, tool_response):
        identity = identities.get(context_id(tool_context))
        try:
            result = await call(callbacks.get("after_tool_callback"), tool=tool, args=args,
                                tool_context=tool_context, tool_response=tool_response)
        except (Exception, asyncio.CancelledError) as error:
            if identity:
                # The real tool already returned even if an observer then failed.
                archive.finish_tool(identity, tool_response)
            raise
        if identity:
            archive.finish_tool(identity, result if result is not None else tool_response)
        return result

    async def on_error(tool, args, tool_context, error):
        identity = identities.get(context_id(tool_context))
        if identity is None and context_id(tool_context):
            # SDK name resolution may fail before before_tool_callback runs.
            identity = trace.archive_tool_start(role, context_id(tool_context), getattr(tool, "name", "tool"), args)
            identities[context_id(tool_context)] = identity
        try:
            result = await call(callbacks.get("on_tool_error_callback"), tool=tool, args=args,
                                tool_context=tool_context, error=error)
        except (Exception, asyncio.CancelledError) as callback_error:
            if identity:
                archive.error_tool(identity, callback_error)
            raise
        if identity:
            if result is None:
                archive.error_tool(identity, error)
            else:
                # ADK uses a non-None replacement as the function response;
                # after_tool_callback is not guaranteed on this recovery path.
                archive.tool_arguments(identity, args, overridden=True)
                archive.finish_tool(identity, result)
        return result  # None preserves the SDK's real exception behavior.

    return {**callbacks, "before_tool_callback": before_tool,
            "after_tool_callback": after_tool, "on_tool_error_callback": on_error}
