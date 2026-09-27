"""Wait for durable conversation work instead of assuming synchronous dispatch."""

import asyncio


async def wait_for_conversation_run(services, message_id, agent_id):
    async with asyncio.timeout(5):
        while True:
            for run in services.run_manager.list_runs(agent_id=agent_id):
                if message_id in run.lifecycle.get("delivery_message_ids", []):
                    return run
            await asyncio.sleep(.01)


async def wait_for_conversation_delivery(services, conversation_id, session_id):
    # Run termination precedes the asynchronous final message and delivery ack.
    async with asyncio.timeout(5):
        while services.conversations.page_messages(conversation_id, session_id).deliveries:
            await asyncio.sleep(.01)
