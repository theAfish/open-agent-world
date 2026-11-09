"""Bind installed Pack dependency recipes to a live authorized Sandbox command.

Managed profiles prepare inside an ordinary Sandbox command; shared profiles
use OAW's existing host bootstrap and read-only Sandbox Python. No provider
calls, installers, package imports, or filesystem mutations occur in resolution.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re

from packaging.specifiers import SpecifierSet
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

from .materialization import RuntimeBundle, RuntimeMount
from .models import SandboxValidationError, execution_command_id
from .python_runtime import validate_requirements
from .pack_profile_runner import validate_command


RESERVED_DIRECTORY = "_oaw_profile"
PYTHON_NAMES = {"python", "python3", "python.exe", "python3.exe"}
NODE_REQUIREMENT = r"(?:@[a-z0-9][a-z0-9._-]*/)?[a-z0-9][a-z0-9._-]*(?:@(?:[0-9][0-9A-Za-z.+~-]*|latest))?"


def environment_digest(declaration) -> str:
    value = declaration.model_dump(mode="json")
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def unsupported_profile_reason(declaration) -> str | None:
    """Static provider bounds shared by installation plans and live admission."""
    if declaration.target != "sandbox" or declaration.management not in {"managed", "legacy-shared"}:
        return "Skill requirements must reference managed or shared Sandbox environments"
    if declaration.provider not in {"python", "node"}:
        return f"Unsupported Sandbox dependency provider: {declaration.provider}"
    if declaration.management == "legacy-shared" and (declaration.requires_python or declaration.platforms):
        return "Shared Sandbox Python uses the platform's managed interpreter; use a private profile for version or platform constraints"
    if declaration.management == "managed" and declaration.prepare != "on-use":
        return "Sandbox profiles support preparation on use in the selected Sandbox"
    if declaration.lock or declaration.resources or declaration.verify_script or declaration.prerequisites:
        return "This Sandbox profile provider does not support lockfiles, extra resources, verification scripts or external prerequisites yet"
    try:
        validate_requirements(list(declaration.python))
    except ValueError as exc:
        return str(exc)
    if any(len(value) > 500 or not re.fullmatch(NODE_REQUIREMENT, value) for value in declaration.node):
        return "Node profile dependencies must be registry package names with optional versions"
    for constraint in SpecifierSet(declaration.requires_python):
        if constraint.operator == "===" or not re.fullmatch(r"\d+(?:\.\d+)*(?:\.\*)?", constraint.version):
            return "Sandbox Python versions must use numeric version constraints"
    return None


def resolve_profiles(registry, references) -> dict:
    python, node, imports, constraints, sources = [], [], [], [], []
    private = False
    for reference in references:
        if not registry.is_enabled(reference.owner):
            raise SandboxValidationError(f"Required Pack is unavailable or disabled: {reference.owner}")
        if reference.owner not in getattr(registry, 'enrolled_pack_owners', {reference.owner}):
            raise SandboxValidationError(f"Required Pack is uninstalled: {reference.owner}")
        pack = registry.pack_specs.get(reference.owner)
        declaration = pack.environments.get(reference.environment) if pack else None
        if declaration is None:
            raise SandboxValidationError(f"Pack environment is not declared: {reference.owner}/{reference.environment}")
        digest = environment_digest(declaration)
        if reference.digest is not None and reference.digest != digest:
            raise SandboxValidationError(f"Pack environment changed: {reference.owner}/{reference.environment}; update the Skill requirement")
        if reason := unsupported_profile_reason(declaration):
            raise SandboxValidationError(reason)
        private = private or declaration.management == "managed"
        try:
            python.extend(validate_requirements(list(declaration.python)))
        except ValueError as exc:
            raise SandboxValidationError(str(exc)) from exc
        for requirement in declaration.node:
            # Index packages only. npm paths, git URLs, flags and shell text are
            # not a declarative dependency and are never command fragments.
            if len(requirement) > 500 or not re.fullmatch(NODE_REQUIREMENT, requirement):
                raise SandboxValidationError("Node profile dependencies must be registry package names with optional versions")
            node.append(requirement)
        imports.extend(declaration.verify_imports)
        for constraint in SpecifierSet(declaration.requires_python):
            if constraint.operator == "===" or not re.fullmatch(r"\d+(?:\.\d+)*(?:\.\*)?", constraint.version):
                raise SandboxValidationError("Sandbox Python versions must use numeric version constraints")
            constraints.append((constraint.operator, constraint.version))
        sources.append({"owner": reference.owner, "environment": reference.environment,
                        "digest": digest, "platforms": list(declaration.platforms)})
    if len(python) > 100 or len(node) > 100:
        raise SandboxValidationError("A composed Sandbox profile supports at most 100 dependencies per ecosystem")
    python_packages = {}
    for value in python:
        requirement = Requirement(value)
        python_packages.setdefault(canonicalize_name(requirement.name), []).append(requirement)
    for name, requirements in python_packages.items():
        pins = {item.version for requirement in requirements for item in requirement.specifier
                if item.operator == "==" and "*" not in item.version}
        if len(pins) > 1 or pins and any(next(iter(pins)) not in requirement.specifier for requirement in requirements):
            raise SandboxValidationError(f"Conflicting Sandbox dependency constraints for {name}")
    node_packages = {}
    for value in node:
        name, separator, version = value.rpartition("@")
        name = name if separator and name else value
        if name in node_packages and node_packages[name] != value:
            raise SandboxValidationError(f"Conflicting Sandbox dependency constraints for {name}")
        node_packages[name] = value
    ownership = None
    if profile_id := getattr(registry, 'pack_profile_id', None):
        enrolled = getattr(registry, 'enrolled_pack_owners', set(registry.pack_specs))
        desired = [{'owner': owner, 'environment': name, 'digest': environment_digest(declaration)}
                   for owner, pack in registry.pack_specs.items() if owner in enrolled
                   for name, declaration in pack.environments.items()
                   if declaration.target == 'sandbox' and declaration.management == 'managed']
        ownership = {'profile': profile_id, 'revision': registry.pack_profile_revision, 'desired': desired}
    recipe = {"format": 1, "python": sorted(set(python)), "node": sorted(set(node)),
            "verify_imports": sorted(set(imports)), "requires_python": sorted(set(constraints)),
            'ownership': ownership,
            "sources": sorted({(item['owner'], item['environment']): item for item in sources}.values(),
                              key=lambda item: (item["owner"], item["environment"]))}
    if sources and not private:
        # PluginEnvironmentBootstrap already aggregates these declarations into
        # SharedPythonRuntime. Never create a private pip installer for them.
        recipe["management"] = "legacy-shared"
    return recipe


def runtime_profile_references(services, agent_id):
    """Only the live invoking runtime's owning Pack can declare its defaults."""
    if agent_id is None:
        return ()
    provider_id = services.run_manager.runtime_provider_id_for_agent(agent_id)
    registry = services.plugins
    if provider_id is None or not registry.has_runtime_provider(provider_id):
        return ()
    owner = registry.runtime_provider_owner_id(provider_id)
    descriptor = next(item for item in registry.plugins() if item.id == owner)
    from open_agent_world.skill_packages import SkillRuntimeRequirement
    return tuple(SkillRuntimeRequirement(owner=owner, environment=name)
                 for name in descriptor.sandbox_environments)


def execution_profile_references(services, agent_id, sandbox_id, *, background_job_id=None):
    if background_job_id is None:
        return runtime_profile_references(services, agent_id)
    # The host supplies a persisted job identity, never arbitrary user profile
    # names. Monitoring retains its provider dependency contract outside a Run.
    from backend.errors import PermissionDeniedError
    job = services.background_jobs.get(background_job_id)
    if job['agent_id'] != agent_id or job['sandbox_id'] != sandbox_id:
        raise PermissionDeniedError('Background job command does not match its Agent and Sandbox')
    services.background_jobs.authorize(job)
    registry = services.plugins
    provider = registry.background_job_provider(job['provider'])
    owner = registry.owner_id('background_job_provider', job['provider'])
    from open_agent_world.skill_packages import SkillRuntimeRequirement
    return tuple(SkillRuntimeRequirement(owner=owner, environment=name)
                 for name in provider.sandbox_environments)


def bind_command_profiles(registry, references, argv):
    """Wrap literal ordinary argv; the existing backend still owns execution."""
    try:
        validate_command(argv)
    except ValueError as exc:
        raise SandboxValidationError(str(exc)) from exc
    recipe = resolve_profiles(registry, references)
    recipe['mode'] = 'command'
    return _wrap_profile(RuntimeBundle('pack-profiles/runtime', ()), recipe, argv)


def _wrap_profile(bundle, recipe, arguments):
    runner = f"{RESERVED_DIRECTORY}/runner.py"
    files = (*bundle.files, (runner, Path(__file__).with_name("pack_profile_runner.py").read_bytes()),
             (f"{RESERVED_DIRECTORY}/recipe.json", json.dumps(recipe, sort_keys=True).encode()))
    wrapped = RuntimeBundle(bundle.key, files, bundle.directories)
    # Per-command identity and argv do not duplicate immutable runtime bundles.
    argv = ["python3", "-I", "-S", runner, execution_command_id.get() or "-", *arguments]
    return argv, RuntimeMount(wrapped, 3)


def bind_profiles(registry, references, bundle: RuntimeBundle, interpreter, script_path, arguments):
    """Return argv/mount for the existing admitted command and OS boundary."""
    recipe = resolve_profiles(registry, references)
    if any(path.casefold().split("/")[0] == RESERVED_DIRECTORY.casefold()
           for path in [*(name for name, _ in bundle.files), *bundle.directories]):
        raise SandboxValidationError(f"Skill files cannot use reserved directory {RESERVED_DIRECTORY}")
    # Custom interpreters may bypass private dependencies. Keep this binding
    # explicit instead of claiming an arbitrary shell sees the environment.
    if len(interpreter) != 1 or interpreter[0].lower() not in PYTHON_NAMES | {"node", "node.exe", 'bash', 'sh'}:
        raise SandboxValidationError("Declared Skill environments require a Python, Node or shell interpreter without custom flags")
    mode = "python" if interpreter[0].lower() in PYTHON_NAMES else interpreter[0].lower().removesuffix('.exe')
    recipe.update(mode=mode, script_path=script_path)
    return _wrap_profile(bundle, recipe, arguments)
