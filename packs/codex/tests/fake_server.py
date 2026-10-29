"""A subprocess fixture, deliberately independent of the adapter implementation."""
import json
import sys
from pathlib import Path

log = Path(sys.argv[1])
sys.stdin.reconfigure(encoding='utf-8')
sys.stdout.reconfigure(encoding='utf-8')


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
    elif method == 'turn/steer':
        assert request['params']['expectedTurnId'] == 'turn-fixture'
        assert request['params']['input'][0]['text'] == 'Skip c'
        if prompt == 'reject-steer':
            send({'id': request['id'], 'error': {'code': -32600, 'message': 'No active turn'}})
            send({'method': 'turn/completed', 'params': {**scope, 'turn': {'id': 'turn-fixture', 'status': 'completed'}}})
            continue
        send({'id': request['id'], 'result': {'turnId': 'turn-fixture'}})
        send({'method': 'item/completed', 'params': {**scope, 'item': {
            'type': 'commandExecution', 'id': 'cmd1', 'status': 'completed', 'exitCode': 0}}})
        send({'method': 'item/completed', 'params': {**scope, 'item': {
            'type': 'agentMessage', 'id': 'msg1', 'text': 'Skipped c after b'}}})
        send({'method': 'turn/completed', 'params': {**scope, 'turn': {'id': 'turn-fixture', 'status': 'completed'}}})
        continue
    elif method == 'turn/start':
        prompt = request['params']['input'][0]['text']
        send({'id': request['id'], 'result': {'turn': {'id': 'turn-fixture'}}})
        scope = {'threadId': 'thread-fixture', 'turnId': 'turn-fixture'}
        if prompt == 'crash':
            sys.exit(7)
        if prompt == 'thinking':
            assert request['params']['summary'] == 'concise'
            for index, delta in [(0, 'Inspect '), (0, 'the files.'), (1, 'Check the result.')]:
                send({'method': 'item/reasoning/summaryTextDelta', 'params': {**scope,
                    'itemId': 'thought1', 'summaryIndex': index, 'delta': delta}})
            send({'method': 'item/reasoning/textDelta', 'params': {**scope,
                'itemId': 'thought1', 'delta': 'private reasoning'}})
            send({'method': 'item/completed', 'params': {**scope, 'item': {'type': 'reasoning',
                'id': 'thought1', 'summary': ['Inspect the files.', 'Verify the result.'], 'content': ['private reasoning']}}})
        if prompt in ('hang', 'reject-steer'):
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
        if prompt == 'minister':
            send({'id': 'minister-list', 'method': 'item/tool/call', 'params': {**scope, 'callId': 'minister-list', 'tool': 'oaw_list_tools', 'arguments': {}}})
            listed = receive()
            assert 'canvas_inspect' in json.dumps(listed)
            tools = json.loads(listed['result']['contentItems'][0]['text'])
            # Exercise the regular Codex bridge with the host's real role tools.
            inspect_tool = next(item for item in tools if item['name'] == 'canvas_inspect')
            minister = inspect_tool['input_schema']['properties']['minister']['enum'][0]
            send({'id': 'minister-inspect', 'method': 'item/tool/call', 'params': {**scope, 'callId': 'minister-inspect', 'tool': 'oaw_invoke_tool',
                'arguments': {'capability_id': inspect_tool['capability_id'], 'arguments': {'minister': minister}}}})
            assert receive()['result']['success'] is True
        send({'method': 'item/agentMessage/delta', 'params': {**scope, 'itemId': 'msg1', 'delta': 'Hello '}})
        send({'method': 'item/agentMessage/delta', 'params': {**scope, 'itemId': 'msg1', 'delta': 'OAW'}})
        send({'method': 'item/completed', 'params': {**scope, 'item': {'type': 'agentMessage', 'id': 'msg1', 'text': 'Hello OAW'}}})
        send({'method': 'turn/completed', 'params': {**scope, 'turn': {'id': 'turn-fixture', 'status': 'failed' if prompt == 'fail' else 'completed', 'error': {'message': 'fixture failure'} if prompt == 'fail' else None}}})
        continue
    send({'id': request['id'], 'result': result})
