"""A subprocess fixture, deliberately independent of the adapter implementation."""
import json
import sys
from pathlib import Path

log = Path(sys.argv[1])


def send(message):
    print(json.dumps(message), flush=True)


def receive():
    line = sys.stdin.readline()
    if not line:
        sys.exit(0)
    message = json.loads(line)
    with log.open('a', encoding='utf-8') as output:
        output.write(json.dumps(message) + '\n')
    return message


while True:
    request = receive()
    method = request.get('method')
    if 'id' not in request:
        continue
    result = {}
    if method in ('thread/start', 'thread/resume'):
        result = {'thread': {'id': request['params'].get('threadId', 'thread-fixture')}}
    elif method == 'turn/start':
        prompt = request['params']['input'][0]['text']
        send({'id': request['id'], 'result': {'turn': {'id': 'turn-fixture'}}})
        scope = {'threadId': 'thread-fixture', 'turnId': 'turn-fixture'}
        if prompt == 'crash':
            sys.exit(7)
        if prompt == 'hang':
            send({'method': 'item/started', 'params': {**scope, 'item': {'type': 'commandExecution', 'id': 'cmd1', 'command': 'sleep'}}})
            continue
        if prompt == 'approval':
            send({'id': 'approval-1', 'method': 'item/commandExecution/requestApproval', 'params': scope})
            receive()
            continue
        if prompt in ('tool', 'revoke'):
            send({'id': 'tool-1', 'method': 'item/tool/call', 'params': {**scope, 'callId': 'call-1', 'tool': 'oaw_list_tools', 'arguments': {}}})
            receive()
            send({'id': 'tool-2', 'method': 'item/tool/call', 'params': {**scope, 'callId': 'call-2', 'tool': 'oaw_invoke_tool', 'arguments': {'capability_id': 'text.edit:notes', 'arguments': {'content': 'updated'}}}})
            reply = receive()
            if prompt == 'tool':
                assert reply['result']['success'] is True
            else:
                assert reply['result']['success'] is False
        send({'method': 'item/agentMessage/delta', 'params': {**scope, 'itemId': 'msg1', 'delta': 'Hello '}})
        send({'method': 'item/agentMessage/delta', 'params': {**scope, 'itemId': 'msg1', 'delta': 'OAW'}})
        send({'method': 'item/completed', 'params': {**scope, 'item': {'type': 'agentMessage', 'id': 'msg1', 'text': 'Hello OAW'}}})
        send({'method': 'turn/completed', 'params': {**scope, 'turn': {'id': 'turn-fixture', 'status': 'failed' if prompt == 'fail' else 'completed', 'error': {'message': 'fixture failure'} if prompt == 'fail' else None}}})
        continue
    send({'id': request['id'], 'result': result})
