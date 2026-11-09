"""One versioned measurement contract for Sandbox output, documentation and validation."""
from __future__ import annotations

import json
import re
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
from .workflow import finite_number


def relative_path(value):
    return (isinstance(value, str) and 0 < len(value) <= 512
            and not any(c in value for c in "\\:\0")
            and all(p not in {"", ".", ".."} for p in value.split("/")))


Name = Annotated[str, Field(min_length=1, max_length=200)]
Count = Annotated[int, Field(ge=0)]
def finite_value(value):
    if not finite_number(value):
        raise ValueError("number must be finite and representable as a float")
    return value


Number = Annotated[int | float, AfterValidator(finite_value)]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class Objective(Contract):
    name: Name
    value: Number
    direction: Literal["minimize", "maximize"]
    unit: Annotated[str, Field(max_length=200)] = ""


class Control(Contract):
    name: Name
    passed: bool
    reason: Annotated[str, Field(max_length=400)] = ""


class Constraint(Control):
    check_ids: Annotated[list[Name], Field(max_length=12)] = Field(default_factory=list,
        description="HOST requirement IDs this executed check covers; partial checks never imply all requirements passed.")
    regions: Annotated[list[Name], Field(max_length=12)] = Field(default_factory=list,
        description="Required when check_ids is nonempty: keys in coverage identifying the actual tested populations, including allowed boundaries separately.")
    controls: Annotated[list[Control], Field(max_length=8)] = Field(default_factory=list,
        description="Tests of the checker on known passing/failing controls. passed means the expected verdict was obtained.")

    @model_validator(mode="after")
    def covered_population(self):
        if self.check_ids and not self.regions:
            raise ValueError("check_ids requires regions naming the populations actually tested in coverage")
        return self


class Frame(Contract):
    reference: Name | None = Field(default=None, description=
        "Shared physical reference ID for mapping between saved coordinate systems. Never infer a mapping from row order or unshifted nearest neighbours.")
    origin: Annotated[list[Number], Field(min_length=3, max_length=3)]
    axes: Annotated[list[Annotated[list[Number], Field(min_length=3, max_length=3)]], Field(min_length=3, max_length=3)]

    @field_validator("axes")
    @classmethod
    def nonsingular(cls, a):
        det = sum(a[0][i] * (a[1][(i+1)%3] * a[2][(i+2)%3] - a[1][(i+2)%3] * a[2][(i+1)%3]) for i in range(3))
        if not finite_number(det) or abs(det) < 1e-12:
            raise ValueError("axes must be nonsingular")
        return a


class ArtifactLayout(Contract):
    format: Literal["npz", "npy", "json", "xyz"]
    primary_dataset: Name | None = Field(default=None, description=
        "NPY: array; NPZ/JSON: actual key; XYZ: atoms; extended XYZ: atoms or extxyz_atoms. "
        "XYZ property names are inspected from Properties, not invented dataset names.")
    subsets: Annotated[dict[Name, Name], Field(max_length=8)] = Field(default_factory=dict,
        description="Producer-declared subset -> parent dataset names, not a host-verified membership test.")

    @model_validator(mode="after")
    def primary_name(self):
        if self.format == "npy" and self.primary_dataset not in {None, "array"}:
            raise ValueError("NPY primary_dataset must be array")
        if self.format == "xyz" and self.primary_dataset not in {None, "atoms", "extxyz_atoms"}:
            raise ValueError("XYZ primary_dataset must be atoms or extxyz_atoms (requires Properties)")
        return self


class Artifact(Contract):
    path: Annotated[str, Field(min_length=1, max_length=512)]
    units: Annotated[str, Field(min_length=1, max_length=100)]
    frame: Frame
    layout: ArtifactLayout | None = None
    components: Annotated[dict[Name, Count], Field(min_length=1, max_length=8)]

    @model_validator(mode="before")
    @classmethod
    def discard_retired_digest(cls, value):
        # Saved scripts from earlier versions may still emit this field.
        # It is no longer part of the contract and never authorizes an action.
        return {k: v for k, v in value.items() if k != "sha256"} if isinstance(value, dict) else value

    @field_validator("path")
    @classmethod
    def local_path(cls, value):
        if not relative_path(value):
            raise ValueError("use a workspace-relative path without empty, dot or parent segments")
        return value


DATA_LIMITS = {"max_depth": 5, "max_items": 128, "max_string_chars": 1200, "max_key_chars": 200}
DATA_DESCRIPTION = ("Bounded JSON, including nested dictionaries/lists: at most 5 levels below this field, "
    "128 keys/items per collection, 1200 characters per string, 200 per nonempty key, and finite numbers. "
    "Save larger diagnostics/arrays in an artifact; JSON-encoding them as a string does not bypass these limits.")


class BoundedDataError(ValueError):
    def __init__(self, path, value, message, **details):
        super().__init__(message)
        self.path, self.actual_type, self.details = path, type(value).__name__, details


def bounded_data(value, depth=0, path=()):
    if depth > 5:
        raise BoundedDataError(path, value, "data nesting exceeds 5 levels", actual_depth=depth, max_depth=5)
    if value is None or type(value) is bool or finite_number(value):
        return value
    if isinstance(value, str):
        if len(value) > 1200:
            raise BoundedDataError(path, value, "string exceeds 1200 characters; save this diagnostic in an artifact",
                                   actual_chars=len(value), max_chars=1200)
        return value
    if isinstance(value, list):
        if len(value) > 128:
            raise BoundedDataError(path, value, "list exceeds 128 items; save this array in an artifact",
                                   actual_items=len(value), max_items=128)
        for index, item in enumerate(value):
            bounded_data(item, depth+1, (*path, index))
        return value
    if isinstance(value, dict):
        if len(value) > 128:
            raise BoundedDataError(path, value, "dictionary exceeds 128 keys; save this data in an artifact",
                                   actual_items=len(value), max_items=128)
        for key, item in value.items():
            if not isinstance(key, str) or not 0 < len(key) <= 200:
                raise BoundedDataError(path, value, "dictionary keys must be nonempty strings of at most 200 characters")
            bounded_data(item, depth+1, (*path, key))
        return value
    raise BoundedDataError(path, value, "use JSON data with finite numbers; do not replace invalid numbers with invented measurements")


class Measurement(Contract):
    schema_version: Literal[1] = 1
    kind: Literal["baseline", "candidate", "diagnostic"] | None = None
    objective: Objective | None = None
    measurement_revision: Name | None = None
    constraints: Annotated[list[Constraint], Field(max_length=12)] = Field(default_factory=list)
    coverage: Annotated[dict[Name, Count], Field(max_length=12)] = Field(default_factory=dict,
        description="Flat region -> integer sample count. A zero blocks checks that reference this region, not unrelated checks. Put parameters or nested statistics in values, not coverage.")
    artifacts: Annotated[list[Artifact], Field(max_length=8)] = Field(default_factory=list)
    assumption_invalid: bool = False
    contradiction: Annotated[str, Field(max_length=1200)] = ""
    values: dict[str, Any] = Field(default_factory=dict, description="Ordinary measured values; not acceptance evidence. " + DATA_DESCRIPTION)
    measurements: dict[str, Any] = Field(default_factory=dict, description="Compatible ordinary data field; not acceptance evidence. " + DATA_DESCRIPTION)
    metadata: dict[str, Any] = Field(default_factory=dict, description="Provenance/parameters; not acceptance evidence. " + DATA_DESCRIPTION)

    @field_validator("schema_version", mode="before")
    @classmethod
    def version(cls, value):
        if type(value) is not int:
            raise ValueError("schema_version must be integer 1")
        return value

    @field_validator("values", "measurements", "metadata")
    @classmethod
    def data(cls, value):
        return bounded_data(value)

    @model_validator(mode="after")
    def consistent(self):
        if self.assumption_invalid and not self.contradiction.strip():
            raise ValueError("assumption_invalid requires an executed contradiction")
        if self.kind == "candidate" and (not self.constraints or not self.measurement_revision or not self.coverage):
            raise ValueError("candidate requires constraints, measurement_revision and nonempty coverage")
        for key, values in (("constraint name", [c.name for c in self.constraints]), ("artifact path", [a.path for a in self.artifacts])):
            if len(values) != len(set(values)):
                raise ValueError(f"duplicate {key}")
        for c in self.constraints:
            if any(region not in self.coverage for region in c.regions):
                raise ValueError(f"constraint {c.name}: regions must name coverage keys")
        return self


def measurement_contract(*, include_schema=True):
    """Generated from exactly the same types used by the stdout parser."""
    candidate_example = Measurement.model_validate({"kind": "candidate", "measurement_revision": "example-v1",
        "coverage": {"inspected_entries": 4},
        "constraints": [{"name": "example_requirement", "passed": False,
            "reason": "Illustrative failed verdict; replace with actual executed measurements.",
            "regions": ["inspected_entries"], "check_ids": ["<HOST build requirement ID>"]}],
        "artifacts": [{"path": "candidate.npz", "units": "angstrom", "components": {"component_a": 4},
            "frame": {"origin": [0, 0, 0], "axes": [[1, 0, 0], [0, 1, 0], [0, 0, 1]]},
            "layout": {"format": "npz", "primary_dataset": "positions"}}]})
    return {**({"schema": Measurement.model_json_schema()} if include_schema else {}),
            "reference": {"skill_id": "structure_inspect", "file_path": "references/measurement-contract.json",
                "reader": "read_skill or read_skills with the authorized Skill/toolbox selector"},
            "candidate_example": {"oaw_measurements": candidate_example.model_dump(exclude_defaults=True)},
            "report_kinds": "emit_measurements defaults to diagnostic: useful intermediate evidence, not build completion. "
                "A build candidate needs explicit kind=candidate, measurement_revision, measured constraints with the "
                "actual HOST check_ids and inspected coverage. File/document delivery also needs the actual saved file "
                "in artifacts and structure_path when advancing. The example is a shape illustration, not a verdict, "
                "coordinate frame or requirement ID to copy; preserve actual failures and independent validation.",
            "data_limits": DATA_LIMITS.copy(),
            "encoding": 'Print ONE line: OAW_MEASUREMENTS {"oaw_measurements": <schema object>}. A marked bare object is also accepted.',
            "example": {"oaw_measurements": {"schema_version": 1, "kind": "diagnostic",
                "measurement_revision": "input-v1", "values": {"measured_length": 2.4},
                "coverage": {"input_atoms": 12},
                "constraints": [{"name": "input_population", "passed": True, "regions": ["input_atoms"]}]}},
            "meaning": "Data fields never imply acceptance. Preserve failed checks. Use HOST check_ids for requirement coverage; each check with check_ids needs nonempty regions naming its tested coverage populations. Independent validation remains required. "
                "coverage counts inspected populations, not output matches: for absence checks use inspected_entries/directories and put found_files=0 in values. "
                "An empty tested population cannot prove a check that references it; unused zero counts do not invalidate other evidence. Artifact components may honestly be zero. "
                "Record actual sample_count, search scope/range and untested hypotheses in metadata; a sample verdict does not prove an unexecuted search."}


def coverage_issue(name, count, check_ids=()):
    return {"name": "coverage:" + name, "passed": False,
            "code": "population_not_reported" if count is None else "empty_test_population",
            "field": "oaw_measurements.coverage." + name, "check_ids": list(check_ids),
            "reason": "No inspected population was reported for this region." if count is None else
                "No samples were measured in this declared region.",
            "next_action": "For an absence check, report the actual inspected directory/entry population in coverage "
                "and found_matches=0 in values, with the search scope in metadata. For a property check, no tested "
                "objects means the property remains untested. Do not mark it passed or create synthetic objects to "
                "prove properties of absent candidates; request plan review if the committed conditional check "
                "does not apply to the observed state. Existing acceptance checks remain unchanged."}


def constraint_failures(value):
    failures = [c for c in value.get("constraints", []) if not c["passed"]]
    tested = {region for c in value.get("constraints", []) for region in c.get("regions", [])}
    failures += [coverage_issue(name, count, [identity for c in value.get("constraints", [])
                    if name in c.get("regions", []) for identity in c.get("check_ids", [])])
                 for name,count in value.get("coverage",{}).items() if count == 0 and name in tested]
    failures += [{"name": "control:" + c["name"] + ":" + control["name"], "passed": False,
                  "reason": "The checker did not reproduce its expected control verdict."}
                 for c in value.get("constraints",[]) for control in c.get("controls",[]) if not control["passed"]]
    return failures


def _unique(pairs):
    result = {}
    for key,value in pairs:
        if key in result:
            raise ValueError(f"duplicate field: {key}")
        result[key] = value
    return result


def parse_measurements(stdout):
    """Last marked record wins; malformed evidence is never promoted or replaced by stale evidence."""
    if not isinstance(stdout,str):
        return None, []
    # The native stdout boundary already limits size. Trailing diagnostics must
    # not hide the last report, including an invalid report requiring feedback.
    for line in reversed(stdout.splitlines()):
        raw=line.strip(); marked=raw.startswith("OAW_MEASUREMENTS ")
        if marked:
            raw=raw[len("OAW_MEASUREMENTS "):]
        elif not raw.startswith("{") or '"oaw_measurements"' not in raw:
            continue
        report_file = None
        try:
            # Native Sandbox output already has a 2 MiB bound. Accept its full
            # record for deterministic diagnostic projection before applying
            # the existing compact scientific-report boundary.
            if len(raw.encode("utf-8")) > 2 * 1024 * 1024:
                raise ValueError("record exceeds the native Sandbox output boundary")
            envelope=json.loads(raw,object_pairs_hook=_unique)
            if not isinstance(envelope,dict) or not envelope:
                raise ValueError("expected a nonempty measurement object")
            value=envelope.get("oaw_measurements",envelope if marked else None)
            if not isinstance(value,dict) or not value:
                raise ValueError("oaw_measurements must be a nonempty object")
            metadata = value.get("metadata")
            if isinstance(metadata, dict) and relative_path(metadata.get("oaw_report_file")):
                report_file = metadata["oaw_report_file"]
            # Earlier callers duplicated identical fields at the envelope level.
            # Accept that unambiguous form, but never discard conflicting facts.
            if "oaw_measurements" in envelope:
                for k,v in envelope.items():
                    if k != "oaw_measurements" and (k not in value or value[k] != v):
                        raise ValueError(f"conflicting or unknown envelope field: {k}")
            from .modelling_io import project_diagnostics
            try:
                value, _ = project_diagnostics(value)
            except ValueError:
                # Preserve the contract's precise invalid-number/key location.
                Measurement.model_validate(value)
                raise
            if len(json.dumps(value, ensure_ascii=False)) > 16000:
                raise ValueError("scientific report exceeds 16000 characters; save diagnostics as artifacts")
            parsed=Measurement.model_validate(value).model_dump(exclude_unset=True)
            return parsed, []
        except ValidationError as exc:
            issues=[]
            for error in exc.errors(include_url=False)[:8]:
                bounded = error.get("ctx", {}).get("error")
                location = (*error["loc"], *bounded.path) if isinstance(bounded, BoundedDataError) else error["loc"]
                issues.append({"code":"measurement_contract",
                    "field":"oaw_measurements"+"".join(f"[{p}]" if isinstance(p,int) else "."+p for p in location),
                    "pointer":"/oaw_measurements"+"".join("/"+str(p).replace("~", "~0").replace("/", "~1") for p in location),
                    "message":error["msg"],
                    "actual_type":bounded.actual_type if isinstance(bounded, BoundedDataError) else type(error.get("input")).__name__,
                    **(bounded.details if isinstance(bounded, BoundedDataError) else {}),
                    "next_action":"Repair only this producer field using HOST measurement_contract. Keep bounded nested data; save oversized diagnostics as artifacts, not long JSON strings. Do not replan the science or invent a metric."})
            if report_file and issues:
                issues[0]["saved_report_file"] = report_file
            return None,issues
        except (ValueError,TypeError,AttributeError,OverflowError,RecursionError) as exc:
            return None,[{"code":"measurement_contract","field":"oaw_measurements","message":str(exc),
                         "next_action":"Repair only the producer record using HOST measurement_contract; keep the measured data and scientific plan.",
                         **({"saved_report_file": report_file} if report_file else {})}]
    return None, []


# Input inspection runs ONLY through native Sandbox execution. It checks paths
# and data layout; it never imports or evaluates model-generated code.
INPUT_GUARD = '''import ast, io, json, math, pathlib, re, shlex, struct, sys, zipfile
root = pathlib.Path.cwd().resolve()
inputs = json.loads(sys.argv[1])
metadata = []

def fail(message, item, **details):
    print(json.dumps({"oaw_input_error": {"path": item["path"],
                     "message": message, **details}}, sort_keys=True))
    raise SystemExit("OAW " + message + ": " + item["path"])

def xyz_description(raw):
    lines = raw.decode("utf-8").splitlines()
    count = int(lines[0])
    if count < 0 or len(lines) < count + 2 or any(line.strip() for line in lines[count+2:]):
        raise ValueError("XYZ needs one frame with exactly the declared atom count")
    # Plain XYZ comments are arbitrary text (including unmatched quotes).
    fields = (dict(token.split("=", 1) for token in shlex.split(lines[1]) if "=" in token)
              if re.search(r"(?:^|\\s)(?:Properties|Lattice|pbc)=", lines[1]) else {})
    properties = fields.get("Properties")
    specs = []
    if properties is not None:
        parts = properties.split(":")
        if not parts or len(parts) % 3 or len(parts) > 128 * 3:
            raise ValueError("invalid extended XYZ Properties")
        for i in range(0, len(parts), 3):
            name, kind, width = parts[i], parts[i+1], int(parts[i+2])
            if not name or name in {"atoms", "extxyz_atoms"} or len(name) > 200 or kind not in {"S", "I", "R", "L"} or not 1 <= width <= 128:
                raise ValueError("invalid extended XYZ property")
            if any(s[0] == name for s in specs):
                raise ValueError("duplicate extended XYZ property")
            specs.append((name, kind, width))
        if not {("species", "S", 1), ("pos", "R", 3)} <= set(specs):
            raise ValueError("extended XYZ requires species:S:1 and pos:R:3")
    else:
        specs = [("species", "S", 1), ("pos", "R", 3)]
    width = sum(s[2] for s in specs)
    for row in lines[2:count+2]:
        values = shlex.split(row)
        if len(values) != width:
            raise ValueError("XYZ atom row does not match declared columns")
        offset = 0
        for name, kind, size in specs:
            for value in values[offset:offset+size]:
                if kind == "R" and not math.isfinite(float(value)):
                    raise ValueError("nonfinite XYZ coordinate/property")
                if kind == "I":
                    int(value)
                if kind == "L" and value.lower() not in {"t", "f", "true", "false", "0", "1"}:
                    raise ValueError("invalid XYZ logical property")
            offset += size
    datasets = {"atoms": {"shape": [count, width], "row_count_verified": True}}
    if properties is not None:
        datasets["extxyz_atoms"] = dict(datasets["atoms"])
    for name, kind, size in specs:
        datasets[name] = {"shape": [count, size], "type": kind}
    description = {"format": "xyz", "declared_atom_count": count, "datasets": datasets,
                   "extended": properties is not None, "header_only": False}
    if "Lattice" in fields:
        cell = [float(v) for v in fields["Lattice"].split()]
        if len(cell) != 9 or not all(math.isfinite(v) for v in cell):
            raise ValueError("invalid extended XYZ Lattice")
        description["cell"] = [cell[i:i+3] for i in range(0, 9, 3)]
    if "pbc" in fields:
        pbc = fields["pbc"].lower().split()
        if len(pbc) != 3 or any(v not in {"t", "f", "true", "false", "0", "1"} for v in pbc):
            raise ValueError("invalid extended XYZ pbc")
        description["pbc"] = [v in {"t", "true", "1"} for v in pbc]
    return description

def npy_header(stream):
    if stream.read(6) != b"\\x93NUMPY":
        raise ValueError("invalid NPY magic")
    version = stream.read(2)
    width = 2 if version == b"\\x01\\x00" else 4 if version in (b"\\x02\\x00", b"\\x03\\x00") else 0
    if not width:
        raise ValueError("unsupported NPY version")
    size = int.from_bytes(stream.read(width), "little")
    if size > 65536:
        raise ValueError("NPY header too large")
    header = ast.literal_eval(stream.read(size).decode("utf-8" if version[0] == 3 else "latin1"))
    shape = header["shape"]
    if not isinstance(shape, tuple) or len(shape) > 32 or any(type(n) is not int or not 0 <= n <= 2**63 - 1 for n in shape):
        raise ValueError("invalid NPY shape")
    dtype = header["descr"]
    if not isinstance(dtype, (str, list)):
        raise ValueError("invalid NPY dtype")
    if type(header.get("fortran_order")) is not bool:
        raise ValueError("invalid NPY array order")
    dtype_text = dtype if isinstance(dtype, str) else repr(dtype)
    return {"shape": list(shape), "dtype": dtype_text[:200], "dtype_truncated": len(dtype_text) > 200,
            "fortran_order": header.get("fortran_order"), "header_only": True}

def inspect(raw, path):
    if raw.startswith(b"\\x93NUMPY"):
        return {"format": "npy", "datasets": {"array": npy_header(io.BytesIO(raw))}}
    if raw.startswith(b"PK"):
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            members = [m for m in archive.infolist() if m.filename.endswith(".npy")]
            datasets = {}
            for member in members[:128]:
                key = member.filename[:-4]
                if len(key) > 200:
                    raise ValueError("NPZ dataset name too long")
                if key in datasets:
                    raise ValueError("duplicate NPZ dataset")
                with archive.open(member) as stream:
                    datasets[key] = npy_header(stream)
            return {"format": "npz", "datasets": datasets, "dataset_count": len(members), "truncated": len(members) > 128}
    if path.suffix.lower() == ".json":
        value = json.loads(raw)
        entries = list(value.items()) if isinstance(value, dict) else [("root", value)]
        return {"format": "json", "datasets": {str(k): {"type": type(v).__name__,
                **({"length": len(v)} if isinstance(v, (list, dict, str)) else {})} for k, v in entries[:128]},
                "dataset_count": len(entries), "truncated": len(entries) > 128}
    if path.suffix.lower() in (".xyz", ".extxyz"):
        return xyz_description(raw)
    return {"format": "opaque"}

for item in inputs:
    path = (root / item["path"]).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        fail("input missing or outside workspace", item)
    with path.open("rb") as stream:
        raw = stream.read(16 * 1024 * 1024 + 1)
    if len(raw) > 16 * 1024 * 1024:
        fail("input exceeds 16 MiB", item)
    try:
        description = inspect(raw, path)
    except (ValueError, KeyError, TypeError, UnicodeError, EOFError, zipfile.BadZipFile, RuntimeError, OSError, RecursionError, SyntaxError, IndexError) as exc:
        description = {"format": "unavailable", "reason": str(exc)[:200]}
    layout = item.get("layout") or {}
    if layout and description["format"] != layout["format"]:
        fail("input format does not match producer layout", item, field="layout.format",
             expected=layout["format"], observed=description)
    datasets = description.get("datasets", {})
    if layout.get("primary_dataset") and layout["primary_dataset"] not in datasets:
        fail("primary dataset missing or unavailable", item, field="layout.primary_dataset",
             expected=layout["primary_dataset"], observed=description)
    if any(k not in datasets or parent not in datasets for k, parent in layout.get("subsets", {}).items()):
        fail("declared subset dataset missing", item, field="layout.subsets", observed=description)
    metadata.append({"path": item["path"], **description,
                     "source": "native Sandbox byte/header inspection", "subset_membership_verified": False})
print(json.dumps({"oaw_inputs_verified": inputs, "oaw_input_metadata": metadata}, sort_keys=True))
'''
