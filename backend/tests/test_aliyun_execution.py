"""Exercise the actual shipped relay over a local SDK transport, without cloud writes."""
import asyncio
from contextlib import aclosing
from dataclasses import replace
import inspect
import json
from pathlib import Path
import shlex
import sys
from types import SimpleNamespace

import pytest
from e2b.sandbox_async.commands.command import Commands

from backend.agents.environment import subprocess_environment
from backend.agents.execution import NativeRuntimeProcess, RuntimeExecutionRequest
from backend.cloud.aliyun import execution as aliyun_execution
from backend.cloud.aliyun.execution import AliyunRuntimeExecutionProvider
from backend.errors import RuntimeUnavailableError
from backend.sandbox.models import SandboxSecurityError
from backend.tests.test_aliyun_sandbox import FakeSDK
from backend.cloud.aliyun.sandbox import AliyunSandboxSettings


def settings():
    return AliyunSandboxSettings('cn-hangzhou','private-control-key','template','alice-volume','alice-workspace')


class DuplexSDK(FakeSDK):
    def __init__(self,root):
        super().__init__(root)
        self.handles={}
        self.file_uploads=[]
        self.file_downloads=[]
        self.fail_kill=False
        self.renewals=[]
        self.renewed=asyncio.Queue()
        self.fail_renew=False

    async def create(self,**kwargs):
        sandbox=await super().create(**kwargs)
        sdk=self
        async def renew(seconds):
            if sdk.fail_renew:
                raise ConnectionError('private-sdk-token-do-not-leak')
            sdk.renewals.append((sandbox.sandbox_id,seconds))
            sdk.renewed.put_nowait(seconds)
        sandbox.set_timeout=renew
        class RemoteCommands:
            async def run(self,command,**options):
                inspect.signature(Commands.run).bind(self,command,**options)
                argv=shlex.split(command)
                if argv[0]=='exec':
                    argv.pop(0)
                argv[0]=sys.executable
                process=await asyncio.create_subprocess_exec(*argv,stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE,
                    env=subprocess_environment(inherit=False),limit=48*1024*1024,
                    **({'creationflags':0x08000000} if sys.platform=='win32' else {'start_new_session':True}))
                native=NativeRuntimeProcess(process)
                streams=[]
                async def pump(channel,callback):
                    while data:=await channel.read(16384):
                        result=callback(data.decode())
                        if inspect.isawaitable(result):await result
                streams.append(asyncio.create_task(pump(process.stdout,options['on_stdout'])))
                streams.append(asyncio.create_task(pump(process.stderr,options['on_stderr'])))
                class Handle:
                    pid=process.pid
                    pending=bytearray()
                    initialized=False
                    async def send_stdin(self,data,request_timeout=None):
                        assert len(data)<=64*1024
                        if self.initialized:
                            # The SDK forwards bytes immediately, including incomplete
                            # JSON lines. Buffer only the fixture's initial path rewrite.
                            await native.write(data)
                            return
                        self.pending.extend(data)
                        if b'\n' in self.pending:
                            line,_,rest=self.pending.partition(b'\n')
                            self.pending.clear()
                            value=json.loads(line)
                            assert value['method']=='initialize'
                            for name in ('workspace','home'):
                                value['params'][name]=str(sdk.root/'volume'/value['params'][name].removeprefix('/workspace/'))
                            await native.write(json.dumps(value).encode()+b'\n')
                            self.initialized=True
                            if rest:
                                await native.write(rest)
                    async def wait(self):
                        code=await native.wait()
                        await asyncio.gather(*streams)
                        return SimpleNamespace(exit_code=code)
                    async def disconnect(self):
                        for task in streams:task.cancel()
                        await asyncio.gather(*streams,return_exceptions=True)
                sdk.handles[sandbox.sandbox_id]=native
                sdk.commands.append((command,options))
                return Handle()
        sandbox.commands=RemoteCommands()
        async def write_file(path, data, **options):
            assert sys.platform=='linux' and len(data)<=256*1024
            assert options=={'user':'user','request_timeout':20}
            with open(path,'wb') as output:
                output.write(data)
            sdk.file_uploads.append((path,len(data)))
        async def read_file(path, **options):
            from e2b.sandbox_async.filesystem.filesystem import Filesystem
            inspect.signature(Filesystem.read).bind(None, path, **options)
            assert sys.platform=='linux' and options=={'format':'stream','user':'user','request_timeout':20}
            assert path.startswith('/proc/') and '/fd/' in path
            sdk.file_downloads.append(path)
            async def chunks():
                with open(path,'rb') as source:
                    while chunk:=source.read(16384):
                        yield chunk
                        await asyncio.sleep(0)
            return aclosing(chunks())
        sandbox.files=SimpleNamespace(write=write_file,read=read_file)
        return sandbox

    async def kill(self,remote_id,**kwargs):
        if self.fail_kill:raise ConnectionError('Termination outcome unknown')
        if remote_id in self.handles:await self.handles[remote_id].close()
        return await super().kill(remote_id,**kwargs)


def request(tmp_path,operation='run',node='agent'):
    return RuntimeExecutionRequest(node,operation,tmp_path/'must-not-exist',tmp_path/'unused-home',
        {'OAW_ALIYUN_SANDBOX_API_KEY':'host-only'}, {'DEEPSEEK_API_KEY':'approved-tool-fixture'},'any.harness')


@pytest.fixture
def lease_clock(monkeypatch):
    """Advance only the provider lease timer; subprocess/SDK I/O stays real."""
    clock=SimpleNamespace(now=100.0,waiting=asyncio.Queue(),release=asyncio.Queue())
    async def sleep(delay):
        clock.waiting.put_nowait(delay)
        await clock.release.get()
        clock.now+=delay
    async def step():
        delay=await asyncio.wait_for(clock.waiting.get(),3)
        clock.release.put_nowait(None)
        return delay
    clock.step=step
    monkeypatch.setattr(aliyun_execution,'time',SimpleNamespace(monotonic=lambda:clock.now))
    monkeypatch.setattr(aliyun_execution,'asyncio',SimpleNamespace(**{**vars(asyncio),'sleep':sleep}))
    return clock


@pytest.mark.asyncio
async def test_runtime_owns_private_networked_vm_and_persists_only_its_volume(tmp_path):
    sdk=DuplexSDK(tmp_path)
    provider=AliyunRuntimeExecutionProvider(tmp_path/'bindings',settings(),sdk=sdk)
    assert provider.configuration('any.harness')['configured']
    assert provider.configuration('any.harness')['max_execution_seconds']==3600
    assert provider.configuration('any.harness')['lease_seconds']==600
    assert provider.configuration('any.harness')['renewable'] is True
    assert not sdk.created
    one=await provider.open(request(tmp_path))
    try:
        assert one.environment['DEEPSEEK_API_KEY']=='approved-tool-fixture'
        assert 'OAW_ALIYUN_SANDBOX_API_KEY' not in one.environment
        assert one.policy.isolation=='dedicated-vm'
        assert one.policy.default_permission=='workspace-write'
        await one.write_file('result.txt',b'persistent')
        process=await one.spawn([sys.executable,'-I','-u','-c','print("ready",flush=True)'],env={})
        assert (await process.readline()).strip()==b'ready'
        assert await process.wait()==0
    finally:
        await one.close()
    assert len(sdk.killed)==1 and not sdk.instances
    two=await provider.open(request(tmp_path,'next-run'))
    try:
        assert await two.read_file('result.txt')==b'persistent'
        assert len(sdk.created)==2
        created=sdk.created[-1]
        assert created['timeout']==600
        assert sdk.commands[-1][1]['timeout']==0
        assert created['allow_internet_access'] and created['secure']
        assert created['network']['allow_public_traffic'] is False
        assert '169.254.0.0/16' in created['network']['deny_out']
        assert created['volume_mounts']=={'/workspace':settings().volume}
        assert sdk.commands[-1][1]['user']=='user'
    finally:
        await two.close()
    assert not (tmp_path/'must-not-exist').exists()


@pytest.mark.asyncio
async def test_active_runtime_renews_same_vm_past_initial_lease_and_stops_on_close(tmp_path,lease_clock):
    sdk=DuplexSDK(tmp_path)
    execution=await AliyunRuntimeExecutionProvider(tmp_path/'bindings',settings(),sdk=sdk,
        lifetime_seconds=3600,lease_seconds=60).open(request(tmp_path))
    pid=execution.transport.handle.pid
    try:
        for _ in range(4):
            assert await lease_clock.step()==20
            assert await asyncio.wait_for(sdk.renewed.get(),3)==60
        assert lease_clock.now==180  # 80 seconds of a 60-second initial lease.
        assert execution.transport.handle.pid==pid
        assert sdk.renewals==[('remote-1',60)]*4
        assert len(sdk.created)==len(sdk.commands)==1
        await execution.write_file('after-lease.txt',b'same operation')
        assert await execution.read_file('after-lease.txt')==b'same operation'
    finally:
        await execution.close()
    assert execution.transport.renewal.done()
    assert len(sdk.renewals)==4 and sdk.killed==['remote-1']
    assert json.loads(next((tmp_path/'bindings').glob('*/aliyun-sandbox.json')).read_text())['state']=='stopped'


@pytest.mark.asyncio
async def test_absolute_execution_deadline_stops_renewal_and_disposes_without_replay(tmp_path,lease_clock):
    sdk=DuplexSDK(tmp_path)
    execution=await AliyunRuntimeExecutionProvider(tmp_path/'bindings',settings(),sdk=sdk,
        lifetime_seconds=60,lease_seconds=60).open(request(tmp_path))
    try:
        for remaining in (40,20):
            await lease_clock.step()
            assert await asyncio.wait_for(sdk.renewed.get(),3)==remaining
        await lease_clock.step()
        with pytest.raises(RuntimeUnavailableError,match='60s execution deadline'):
            await asyncio.wait_for(execution.transport.wait(),3)
        with pytest.raises(RuntimeUnavailableError,match='not replayed'):
            await execution.transport.write(b'never resubmit\n')
    finally:
        await execution.close()
    assert sdk.renewals==[('remote-1',40),('remote-1',20)]
    assert len(sdk.created)==len(sdk.commands)==1 and sdk.killed==['remote-1']
    assert execution.transport.closed


@pytest.mark.asyncio
async def test_renewal_failure_fails_streams_and_joins_owned_cleanup(tmp_path,lease_clock):
    sdk=DuplexSDK(tmp_path)
    execution=await AliyunRuntimeExecutionProvider(tmp_path/'bindings',settings(),sdk=sdk,
        lease_seconds=60).open(request(tmp_path))
    sdk.fail_renew=True
    try:
        await lease_clock.step()
        with pytest.raises(RuntimeUnavailableError,match='lease renewal failed.*ConnectionError') as failed:
            await asyncio.wait_for(execution.transport.wait(),3)
        assert 'private-sdk-token' not in str(failed.value)
        for channel in (execution.transport.stdout,execution.transport.stderr):
            assert channel.reader.exception() is failed.value
        with pytest.raises(RuntimeUnavailableError,match='not replayed'):
            await execution.transport.write(b'never resubmit\n')
    finally:
        await execution.close()
    assert not sdk.renewals
    assert len(sdk.created)==len(sdk.commands)==1 and sdk.killed==['remote-1']


@pytest.mark.asyncio
async def test_cancelled_close_joins_inflight_renewal_before_vm_disposal(tmp_path,lease_clock):
    sdk=DuplexSDK(tmp_path)
    execution=await AliyunRuntimeExecutionProvider(tmp_path/'bindings',settings(),sdk=sdk,
        lease_seconds=60).open(request(tmp_path))
    renewing,cancelled,killing,release_kill=(asyncio.Event() for _ in range(4))
    async def held_renewal(seconds):
        renewing.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()
    original_kill=sdk.kill
    async def held_kill(*args,**kwargs):
        assert cancelled.is_set()
        killing.set()
        await release_kill.wait()
        return await original_kill(*args,**kwargs)
    sdk.instances['remote-1'].set_timeout=held_renewal
    sdk.kill=held_kill
    try:
        await lease_clock.step()
        await asyncio.wait_for(renewing.wait(),3)
        closing=asyncio.create_task(execution.transport.close())
        await asyncio.wait_for(killing.wait(),3)
        closing.cancel()
        release_kill.set()
        with pytest.raises(asyncio.CancelledError):
            await closing
        assert execution.transport.closed and sdk.killed==['remote-1']
        assert execution.transport.renewal.done()
    finally:
        release_kill.set()
        await execution.close()


@pytest.mark.asyncio
async def test_renewal_rejects_foreign_owner_and_never_kills_its_vm(tmp_path,lease_clock):
    sdk=DuplexSDK(tmp_path)
    execution=await AliyunRuntimeExecutionProvider(tmp_path/'bindings',settings(),sdk=sdk,
        lease_seconds=60).open(request(tmp_path))
    sdk.instances['remote-1'].metadata['oaw.profile']='another-profile'
    try:
        await lease_clock.step()
        with pytest.raises(RuntimeUnavailableError,match='ownership changed'):
            await asyncio.wait_for(execution.transport.wait(),3)
        with pytest.raises(SandboxSecurityError,match='termination is forbidden'):
            await execution.close()
        assert not sdk.killed and not sdk.renewals
        value=json.loads(next((tmp_path/'bindings').glob('*/aliyun-sandbox.json')).read_text())
        assert value['state']=='uncertain' and value['remote_id']=='remote-1'
    finally:
        # Dispose only the test's local subprocess, not through the cloud adapter.
        await sdk.handles['remote-1'].close()
        await execution.transport.handle.disconnect()
        for task in (execution.reader,execution.stderr):task.cancel()
        await asyncio.gather(execution.reader,execution.stderr,return_exceptions=True)


@pytest.mark.parametrize('option,value',[
    ('lifetime_seconds',59),('lifetime_seconds',86401),('lifetime_seconds',True),
    ('lifetime_seconds',3600.0),('lease_seconds',59),('lease_seconds',3601),
    ('lease_seconds',True),('lease_seconds',600.0),
])
def test_runtime_budgets_reject_invalid_operator_values(tmp_path,option,value):
    with pytest.raises(ValueError):
        AliyunRuntimeExecutionProvider(tmp_path,settings(),**{option:value})


@pytest.mark.asyncio
async def test_environment_permission_requires_an_explicit_operator_policy(tmp_path):
    sdk=DuplexSDK(tmp_path)
    provider=AliyunRuntimeExecutionProvider(tmp_path/'bindings',settings(),sdk=sdk,
        default_permission='environment')
    assert not sdk.created
    assert provider.configuration(None)['default_permission']=='environment'
    execution=await provider.open(request(tmp_path))
    try:
        assert execution.policy.isolation=='dedicated-vm'
        assert execution.policy.default_permission=='environment'
        assert sdk.commands[0][1]['user']=='user'
        assert sdk.created[0]['secure'] and sdk.created[0]['network']['allow_public_traffic'] is False
    finally:
        await execution.close()
    with pytest.raises(ValueError,match='permission'):
        AliyunRuntimeExecutionProvider(tmp_path/'other',settings(),sdk=sdk,default_permission='full')


@pytest.mark.asyncio
async def test_fresh_vm_ownership_is_verified_before_any_execution_or_policy_claim(tmp_path):
    class WrongOwnerSDK(DuplexSDK):
        async def create(self,**kwargs):
            sandbox=await super().create(**kwargs)
            sandbox.metadata['oaw.profile']='another-profile'
            return sandbox
    sdk=WrongOwnerSDK(tmp_path)
    provider=AliyunRuntimeExecutionProvider(tmp_path/'bindings',settings(),sdk=sdk,
        default_permission='environment')
    with pytest.raises(SandboxSecurityError,match='ownership'):
        await provider.open(request(tmp_path))
    assert not sdk.commands and not sdk.killed
    binding=next((tmp_path/'bindings').glob('*/aliyun-sandbox.json'))
    assert json.loads(binding.read_text())['state']=='uncertain'


@pytest.mark.asyncio
async def test_legacy_file_write_crosses_fragmented_sdk_stdin(tmp_path):
    sdk=DuplexSDK(tmp_path)
    provider=AliyunRuntimeExecutionProvider(tmp_path/'bindings',settings(),sdk=sdk)
    execution=await provider.open(request(tmp_path))
    chunk=bytes(range(256))*1024
    try:
        async with asyncio.timeout(15):
            await execution.write_file('attachment.bin',chunk*3)
            assert await execution.read_file('attachment.bin')==chunk*3
    finally:
        await execution.close()
    assert len(sdk.killed)==1 and not sdk.instances


@pytest.mark.asyncio
@pytest.mark.skipif(sys.platform!='linux',reason='Agent Sandbox binary uploads reopen an owned Linux /proc FD')
async def test_streamed_file_uses_binary_sdk_and_preserves_verified_output(tmp_path):
    import hashlib
    sdk=DuplexSDK(tmp_path)
    execution=await AliyunRuntimeExecutionProvider(tmp_path/'bindings',settings(),sdk=sdk).open(request(tmp_path))
    chunk=bytes(range(256))*1024
    count=516  # 129 MiB would exceed the stdout quota even without base64 expansion.
    async def source():
        for _ in range(count):
            yield chunk
    try:
        target=await execution.write_file_stream('attachment.bin',source())
        expected_digest=hashlib.sha256()
        for _ in range(count):
            expected_digest.update(chunk)
        expected=expected_digest.hexdigest()
        with Path(target).open('rb') as content:
            assert hashlib.file_digest(content,'sha256').hexdigest()==expected
        result_digest,size=hashlib.sha256(),0
        async with aclosing(execution.read_file_stream('attachment.bin')) as content:
            async for block in content:
                result_digest.update(block)
                size+=len(block)
        assert size==129*1024*1024 and result_digest.hexdigest()==expected
        assert len(sdk.file_uploads)==len(sdk.file_downloads)==count
        assert all(size==len(chunk) for _,size in sdk.file_uploads)
        assert execution.transport.output_bytes<1024*1024
    finally:
        await execution.close()
    assert len(sdk.killed)==1 and not sdk.instances


@pytest.mark.asyncio
async def test_failed_vm_cleanup_retains_receipt_and_can_be_retried(tmp_path):
    sdk=DuplexSDK(tmp_path)
    provider=AliyunRuntimeExecutionProvider(tmp_path/'bindings',settings(),sdk=sdk)
    execution=await provider.open(request(tmp_path))
    sdk.fail_kill=True
    try:
        with pytest.raises(RuntimeError,match='cleanup could not be confirmed'):
            await execution.close()
        binding=next((tmp_path/'bindings').glob('*/aliyun-sandbox.json'))
        value=json.loads(binding.read_text())
        assert value['state']=='uncertain' and value['remote_id']
        assert not execution.closed
    finally:
        sdk.fail_kill=False
        await execution.close()
    assert json.loads(binding.read_text())['state']=='stopped'


@pytest.mark.asyncio
async def test_stale_binding_cannot_kill_or_reuse_another_profile_vm(tmp_path):
    sdk=DuplexSDK(tmp_path)
    provider=AliyunRuntimeExecutionProvider(tmp_path/'bindings',settings(),sdk=sdk)
    execution=await provider.open(request(tmp_path))
    remote=next(iter(sdk.instances.values()))
    owner=remote.metadata['oaw.profile']
    remote.metadata['oaw.profile']='different-profile'
    try:
        with pytest.raises(SandboxSecurityError,match='ownership'):
            await provider.open(request(tmp_path,'new-after-crash'))
        assert not sdk.killed and len(sdk.created)==1
    finally:
        remote.metadata['oaw.profile']=owner
        await execution.close()
