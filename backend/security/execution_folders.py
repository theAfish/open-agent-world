"""Host-only folder bindings; portable documents carry requirements, not authority."""
import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictStr

from backend.errors import ResourceValidationError
from backend.security.execution_credentials import ExecutionCredentialStore, GlobalExecutionCredentialStore
from backend.sandbox.models import ResourceAccess


class FolderRequirement(BaseModel):
    model_config = ConfigDict(extra="forbid")
    folder_ref: StrictStr = Field(min_length=1, max_length=120, pattern=r"^[A-Za-z0-9_.-]+$")

    @property
    def reference(self):
        return self.folder_ref

    @property
    def kind(self):
        return "folder"


class FileRequirement(BaseModel):
    model_config = ConfigDict(extra="forbid")
    file_ref: StrictStr = Field(min_length=1, max_length=120, pattern=r"^[A-Za-z0-9_.-]+$")

    @property
    def reference(self):
        return self.file_ref

    @property
    def kind(self):
        return "file"


class PathRequirement(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path_ref: StrictStr = Field(min_length=1, max_length=120, pattern=r"^[A-Za-z0-9_.-]+$")

    @property
    def reference(self):
        return self.path_ref

    @property
    def kind(self):
        return "path"


PATH_REQUIREMENTS = (FolderRequirement, FileRequirement, PathRequirement)


def inspect_environment_path(raw, validator, *, kind=None):
    """Resolve and validate a host resource for both preview and binding."""
    from backend.sandbox.environment import host_environment_path
    source = host_environment_path(raw)
    detected = "file" if Path(source).is_file() else "folder"
    path = Path(validator.validate_environment_path(source, kind or detected))
    protected = [Path(os.environ[key]).resolve() for key in
        ("SystemRoot", "ProgramFiles", "ProgramFiles(x86)", "ProgramData") if os.environ.get(key)] if os.name == "nt" else [
        Path(p).resolve() for p in ("/proc", "/sys", "/dev", "/run", "/etc", "/usr", "/bin", "/sbin", "/lib", "/lib64")]
    if any(path.is_relative_to(p) or p.is_relative_to(path) for p in protected):
        raise ResourceValidationError("Path variables may not expose system directories")
    return str(path), detected


class FolderBinding(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: StrictStr = Field(min_length=1, max_length=4096)
    access: ResourceAccess = ResourceAccess.READ_ONLY
    kind: Literal["folder", "file"] = "folder"


class ExecutionFolderStore(ExecutionCredentialStore):
    def _key(self, node_id, reference):
        return super()._key(node_id, reference).replace("execution_credential:", "execution_folder:", 1)

    def folder(self, node_id, reference):
        if not self.configured(node_id, reference):
            raise ResourceValidationError("Path is unbound; choose its host file or directory in Environment variables and save")
        return FolderBinding.model_validate_json(self.resolve(node_id, reference))

    def resource(self, node_id, requirement):
        binding = self.folder(node_id, requirement.reference)
        if requirement.kind != "path" and binding.kind != requirement.kind:
            raise ResourceValidationError("Path type changed; choose the file or folder again and save")
        return binding

    def public(self, node_id, variables):
        return {v.reference: self.folder(node_id, v.reference).model_dump(mode="json")
                for v in variables.values() if isinstance(v, PATH_REQUIREMENTS) and self.configured(node_id, v.reference)}

    def prepare(self, node_id, variables, updates, validator):
        references = {v.reference for v in variables.values() if isinstance(v, PATH_REQUIREMENTS)}
        if set(updates) - references:
            raise ResourceValidationError("Path does not belong to this environment")
        prepared = {}
        kinds = {}
        for name, requirement in variables.items():
            if not isinstance(requirement, PATH_REQUIREMENTS):
                continue
            reference = requirement.reference
            if reference in kinds and kinds[reference] != requirement.kind:
                raise ResourceValidationError("File and folder variables must use different references")
            kinds[reference] = requirement.kind
            if reference in updates:
                binding = FolderBinding.model_validate(updates[reference])
                if requirement.kind != "path" and binding.kind != requirement.kind:
                    raise ResourceValidationError(f"Choose a {requirement.kind} for {name}")
                path, detected = inspect_environment_path(binding.path, validator,
                    kind=None if requirement.kind == "path" else requirement.kind)
                prepared[reference] = binding.model_copy(update={"path": path, "kind": detected})
            elif not self.configured(node_id, reference):
                raise ResourceValidationError(f"Choose a {requirement.kind} for {name}, or remove the unused variable")
            else:
                self.resource(node_id, requirement)
        return prepared

    def save_bindings(self, node_id, variables, old_variables, prepared):
        for reference, binding in prepared.items():
            self.bind(node_id, reference, binding.model_dump_json())
        keep = {v.reference for v in variables.values() if isinstance(v, PATH_REQUIREMENTS)}
        for value in old_variables.values():
            if isinstance(value, PATH_REQUIREMENTS) and value.reference not in keep:
                self.unbind(node_id, value.reference)


class GlobalExecutionFolderStore(ExecutionFolderStore, GlobalExecutionCredentialStore):
    """Reuse the global host binding store with a separate folder namespace."""
    def _key(self, node_id, reference):
        return GlobalExecutionCredentialStore._key(self, node_id, reference).replace("global_execution_credential:", "global_execution_folder:", 1)


def folder_store(services):
    credentials = services.execution_credentials
    return ExecutionFolderStore(credentials.settings_store, services.world)
