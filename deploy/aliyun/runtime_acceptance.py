"""Explicit nonroot acceptance factory for a reviewed external Harness test.

Importing/configuration does not allocate a Sandbox. Run only after approval;
the test's SDK session creates and confirms teardown of its owned VMs.
"""
import json
import os
from pathlib import Path

from backend.cloud.aliyun.execution import AliyunRuntimeExecutionProvider
from backend.cloud.aliyun.sandbox import AliyunSandboxSettings
from backend.hosting.configuration import identifier


def create_provider(settings):
    path=Path(os.environ['OAW_ACCEPTANCE_SANDBOX_SETTINGS'])
    value=json.loads(path.read_text(encoding='utf-8'))
    if set(value) != {'region','template','volume','profile_id','storage_addresses'}:
        raise ValueError('Provide only the approved profile Volume assignment')
    identifier(value['profile_id'])
    if value['volume']!='oaw-'+value['profile_id']:
        raise ValueError('Acceptance must use that profile\'s dedicated Volume')
    remote=AliyunSandboxSettings(**value,api_key=os.environ['OAW_ALIYUN_SANDBOX_API_KEY'])
    return AliyunRuntimeExecutionProvider(settings.data_root/'runtime-executions',remote)
