"""Explicit profile binding for registered callbacks, including async work."""
from contextvars import ContextVar
from dataclasses import replace
from functools import wraps
import inspect
import os
from pathlib import Path

_data_root = ContextVar('oaw_xrd_data_root', default=None)


def current_data_root():
    root = _data_root.get()
    if root is None:
        from open_agent_world.plugin_api import PackEnvironmentManager
        root = PackEnvironmentManager().root.parent
    return root


def roots(data_root=None):
    data_root = Path(data_root) if data_root is not None else current_data_root()
    root = Path(os.environ.get('OAW_XRD_ROOT', str(data_root / 'packs-data' / 'research.xrd')))
    return root, Path(os.environ.get('OAW_XRD_RUN_ROOT', str(root / 'runs')))


def bind(callback, data_root):
    if callback is None:
        return None
    if inspect.iscoroutinefunction(callback):
        @wraps(callback)
        async def asynchronous(*args, **kwargs):
            token = _data_root.set(data_root)
            try:
                return await callback(*args, **kwargs)
            finally:
                _data_root.reset(token)
        return asynchronous
    @wraps(callback)
    def synchronous(*args, **kwargs):
        token = _data_root.set(data_root)
        try:
            return callback(*args, **kwargs)
        finally:
            _data_root.reset(token)
    return synchronous


class BoundObject:
    def __init__(self, target, data_root):
        self.target, self.data_root = target, data_root

    def __getattr__(self, name):
        value = getattr(self.target, name)
        return bind(value, self.data_root) if callable(value) else value


class ProfileRegistration:
    def __init__(self, registration, data_root):
        self.registration, self.data_root = registration, data_root

    def __getattr__(self, name):
        return getattr(self.registration, name)

    def register_capability(self, definition, handler):
        return self.registration.register_capability(definition, bind(handler, self.data_root))

    def register_node_type(self, node):
        wrap = lambda callback: bind(callback, self.data_root)
        updates = {'resource_actions': {name: replace(action, handler=wrap(action.handler))
                   for name, action in node.resource_actions.items()}}
        for name in ('lifecycle', 'template_handler'):
            value = getattr(node, name)
            if value is not None:
                updates[name] = BoundObject(value, self.data_root)
        if node.document:
            document = node.document
            actions = {name: replace(action, handler=wrap(action.handler), prepare=wrap(action.prepare))
                       for name, action in document.actions.items()}
            updates['document'] = replace(document, actions=actions,
                summarize=wrap(document.summarize), capture=wrap(document.capture),
                remap_references=wrap(document.remap_references), validate_update=wrap(document.validate_update),
                downloads={name: wrap(callback) for name, callback in document.downloads.items()})
        return self.registration.register_node_type(replace(node, **updates))
