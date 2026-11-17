"""Chat through existing Agent runs or the persisted Conversation transcript."""
from __future__ import annotations

import sys
import time
from urllib.parse import quote
from uuid import uuid4

from .client import CliError, resolve_one


TERMINAL = {"succeeded", "failed", "cancelled", "interrupted"}


def _path(identity):
    return quote(identity, safe="")


def _select_session(summary, selector):
    sessions = summary["sessions"]
    if selector:
        exact = [item for item in sessions if item["id"] == selector]
        matches = exact or [item for item in sessions if item["title"].casefold() == selector.casefold()]
        matches = matches or [item for item in sessions if item["id"].startswith(selector)]
        return resolve_one(matches, "session", selector)
    if len(sessions) == 1:
        return sessions[0]
    defaults = [item for item in sessions if item.get("is_default")]
    if len(defaults) == 1:
        return defaults[0]
    if not sessions:
        return None
    raise CliError("session_required", "Choose the conversation session.", exit_code=2,
                   hint="Add --session ID or --new.",
                   candidates=[{"id": item["id"], "name": item["title"]} for item in sessions])


def _participant(client, card, summary, session, selector):
    connected = {agent["id"]: agent for agent in summary["agents"] if agent["connected"]}
    if selector:
        agent = client.card(selector)
        if agent["id"] not in connected:
            raise CliError("agent_not_connected", f"'{agent['name']}' is not connected to '{card['name']}'.", exit_code=2,
                           hint=f'oaw connect "{agent["name"]}" "{card["name"]}" participate')
        return connected[agent["id"]]
    participants = [connected[identity] for identity in (session or {}).get("participant_ids", []) if identity in connected]
    if len(participants) == 1:
        return participants[0]
    if not participants and not (session or {}).get("participant_ids") and len(connected) == 1:
        return next(iter(connected.values()))
    choices = participants or list(connected.values())
    if not choices:
        raise CliError("agent_required", f"No Agent is connected to '{card['name']}'.", exit_code=2,
                       hint=f'oaw connect AGENT "{card["name"]}" participate')
    raise CliError("agent_required", "Choose which Agent should reply.", exit_code=2,
                   hint="Add --agent NAME.",
                   candidates=[{"id": item["id"], "name": item["name"]} for item in choices])


def _wait_conversation(client, card, session, posted, agent, timeout):
    deadline = time.monotonic() + timeout
    conversation_id, session_id = card["id"], session["id"]
    message = posted["message"]
    run = None
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise CliError("wait_timeout", "The message is saved; its reply is not finished yet.", exit_code=6)
            client.timeout = min(30, remaining)
            page = client.request("GET", f"/conversations/{_path(conversation_id)}/sessions/{_path(session_id)}/timeline",
                                  limit=100)
            pending = [item for item in page["deliveries"] if item["message_id"] == message["id"]
                       and item["agent_id"] == agent["id"]]
            candidates = set(page["run_summaries"])
            candidates.update(item["run_id"] for item in page["active_runs"])
            candidates.update(item["claimed_run_id"] for item in pending if item.get("claimed_run_id"))
            if run:
                candidates.add(run["run_id"])
            matched = []
            for run_id in candidates:
                candidate = client.request("GET", "/runs/" + _path(run_id))
                if (candidate["agent_id"] == agent["id"] and candidate["caller_id"] == conversation_id
                        and candidate["context_id"] == session_id
                        and message["id"] in candidate["lifecycle"].get("delivery_message_ids", [])):
                    matched.append(candidate)
            if matched:
                run = max(matched, key=lambda item: item["created_at"])
            if run and run["status"] in TERMINAL and not pending:
                result = client.request("GET", "/runs/" + _path(run["run_id"]) + "/result")
                return {"card_name": card["name"], "conversation_id": conversation_id, "session_id": session_id,
                        "message_id": message["id"], "text": result["text"],
                        "replies": [{**result, "agent_name": agent["name"]}]}, 0 if result["status"] == "succeeded" else 7
            time.sleep(min(.25, max(0, deadline - time.monotonic())))
    except KeyboardInterrupt:
        raise CliError("interrupted", "Waiting stopped; the saved message remains in the conversation.", exit_code=130,
                       conversation_id=conversation_id, session_id=session_id, message_id=message["id"],
                       run_id=run["run_id"] if run else None,
                       hint=f'oaw chat "{card["name"]}" --session {session_id} --history') from None
    except CliError as exc:
        exc.error.update(conversation_id=conversation_id, session_id=session_id, message_id=message["id"])
        if run:
            exc.error["run_id"] = run["run_id"]
        exc.error["hint"] = f'The message is already saved. Read it with oaw chat "{card["name"]}" --session {session_id} --history.'
        raise


def _agent_turn(client, card, message, timeout, fresh_context):
    from .main import wait_result
    accepted = client.request("POST", "/agents/" + _path(card["id"]) + "/run",
                              {"prompt": message, "fresh_context": fresh_context})
    result = wait_result(client, {"run_id": accepted["run_id"], "status": "created"}, timeout)
    return {"card_name": card["name"], "conversation_id": None, "session_id": None,
            "message_id": None, "text": result["text"], "replies": [{**result, "agent_name": card["name"]}]}, (
                0 if result["status"] == "succeeded" else 7)


def handle_chat(client, args):
    """Return one reply/history result; an interactive session renders its turns."""
    from .main import read_text

    selector = args.card
    message = getattr(args, "message", None)
    message_file = getattr(args, "message_file", None)
    history = getattr(args, "history", False)
    fresh = getattr(args, "new", False)
    session_selector = getattr(args, "session", None)
    agent_selector = getattr(args, "agent", None)
    json_mode = getattr(args, "json", False)
    plain = getattr(args, "plain", False)
    timeout = getattr(args, "timeout", None) or 300
    if message is not None and message_file is not None:
        raise CliError("invalid_arguments", "Use MESSAGE or --message-file, not both.", exit_code=2)
    if history and (message is not None or message_file is not None or fresh):
        raise CliError("invalid_arguments", "Use --history by itself to read saved messages.", exit_code=2)
    if fresh and session_selector:
        raise CliError("invalid_arguments", "Choose --session or --new.", exit_code=2)
    if message_file is not None:
        message = read_text(message_file)
    interactive = message is None and not history
    if interactive and (json_mode or not sys.stdin.isatty() or not sys.stdout.isatty()):
        raise CliError("message_required", "Provide a message, --message-file, or --history.", exit_code=2)
    if message is not None and not message.strip():
        raise CliError("message_required", "The message cannot be empty.", exit_code=2)

    card = client.card(selector)
    definition = next((item for item in client.catalog()["node_types"] if item["id"] == card["type"]), {})
    is_agent = "core.agent" in definition.get("traits", [])
    if is_agent:
        if session_selector or agent_selector or history or fresh and interactive:
            raise CliError("conversation_required", "Use a Conversation card for sessions and saved chat history.", exit_code=2,
                           hint='oaw create conversation --name Chat')
        session = None
        agent = card
    elif card["type"] == "conversation":
        summary = client.request("GET", "/conversations/" + _path(card["id"]))
        session = None if fresh else _select_session(summary, session_selector)
        if history:
            page = client.request("GET", f"/conversations/{_path(card['id'])}/sessions/{_path(session['id'])}/timeline", limit=100) if session else {"items": [], "has_before": False}
            return {"card_name": card["name"], "conversation_id": card["id"],
                    "session_id": session["id"] if session else None,
                    "messages": page["items"], "has_before": page["has_before"], "replies": []}, "chat", 0
        agent = _participant(client, card, summary, session, agent_selector)
        if session is None:
            session = client.request("POST", "/conversations/" + _path(card["id"]) + "/sessions",
                                     {"participant_ids": [agent["id"]]})
        elif agent["id"] not in session["participant_ids"]:
            session = client.request("POST", f"/conversations/{_path(card['id'])}/sessions/{_path(session['id'])}/participants",
                                     {"participant_ids": [agent["id"]]})
    else:
        raise CliError("not_chat_card", f"'{card['name']}' cannot receive chat messages.", exit_code=2,
                       hint="Choose an Agent or Conversation card.")

    def turn(text):
        client.timeout = 30
        if is_agent:
            return _agent_turn(client, card, text, timeout, fresh)
        message_id = str(uuid4())
        try:
            posted = client.request("POST", f"/conversations/{_path(card['id'])}/sessions/{_path(session['id'])}/messages",
                                    {"message_id": message_id, "content": text, "mention_agent_ids": [agent["id"]]})
        except CliError as exc:
            exc.error.update(conversation_id=card["id"], session_id=session["id"], message_id=message_id)
            exc.error["hint"] = f'Check oaw chat "{card["name"]}" --session {session["id"]} --history before sending again.'
            raise
        return _wait_conversation(client, card, session, posted, agent, timeout)

    if not interactive:
        data, code = turn(message)
        return data, "chat", code
    from .render import Renderer, clean
    renderer = Renderer(plain=plain)
    renderer.value(f"{clean(card['name'])} · /exit to leave")
    if session:
        page = client.request("GET", f"/conversations/{_path(card['id'])}/sessions/{_path(session['id'])}/timeline", limit=10)
        if page["items"]:
            renderer.chat({"messages": page["items"]})
    while True:
        try:
            text = input("You > ").strip()
            if text in {"/exit", "/quit"}:
                return None, "chat", 0
            if not text:
                continue
            data, code = turn(text)
            renderer.chat(data)
        except (EOFError, KeyboardInterrupt):
            return None, "chat", 0
        except CliError as exc:
            renderer.error(exc.error["message"], exc.error.get("hint"))
            if exc.exit_code == 130:
                return None, "chat", 130
