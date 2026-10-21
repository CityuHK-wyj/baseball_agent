"""Sandboxed Python compute: bounded post-processing over explicit Artifact bindings.

The child process receives only the bound rows, runs an AST-validated program with a
restricted builtin set, and returns JSON. It has no network, database, host environment,
arbitrary filesystem or subprocess access, and runs under wall/CPU/memory limits.
"""

from __future__ import annotations

import ast
import json
import resource
import subprocess
import sys
import textwrap
from dataclasses import dataclass, field
from typing import Any

from app.capabilities.base import CapabilityResult, CapabilitySpec
from app.contracts.models import (
    Artifact,
    EnumerationContract,
    Export,
    GlobalOperation,
    Reference,
    ReferenceType,
    Scope,
    ToolOutcome,
    ToolRequest,
)
from app.verification.enumeration import check_compatibility

ALLOWED_MODULES = {"math", "statistics", "datetime", "collections", "itertools", "functools", "json", "re", "decimal"}

FORBIDDEN_NAMES = {
    "open", "eval", "exec", "compile", "__import__", "globals", "locals", "vars", "input",
    "breakpoint", "exit", "quit", "help", "memoryview", "object", "super", "setattr",
    "getattr", "delattr", "classmethod", "staticmethod", "property", "type",
}

RUNNER = textwrap.dedent(
    """
    import json, sys
    payload = json.loads(sys.stdin.read())
    program = payload["program"]
    inputs = payload["inputs"]
    safe_modules = payload["modules"]

    def _import(name, globals=None, locals=None, fromlist=(), level=0):
        root = name.split(".")[0]
        if root not in safe_modules:
            raise ImportError("module not allowed: " + name)
        return __import__(name, globals, locals, fromlist, level)

    safe_builtins = {
        "abs": abs, "all": all, "any": any, "bool": bool, "dict": dict, "divmod": divmod,
        "enumerate": enumerate, "filter": filter, "float": float, "int": int, "len": len,
        "list": list, "map": map, "max": max, "min": min, "pow": pow, "range": range,
        "reversed": reversed, "round": round, "set": set, "sorted": sorted, "str": str,
        "sum": sum, "tuple": tuple, "zip": zip, "isinstance": isinstance, "print": print,
        "__import__": _import, "Exception": Exception, "ValueError": ValueError,
        "KeyError": KeyError, "IndexError": IndexError, "ZeroDivisionError": ZeroDivisionError,
        "True": True, "False": False, "None": None,
    }
    scope = {"__builtins__": safe_builtins, "inputs": inputs, "json": json}
    exec(compile(program, "<python_compute>", "exec"), scope)
    output = scope.get("output")
    sys.stdout.write(json.dumps({"ok": True, "output": output}, default=str))
    """
)


class ComputeError(ValueError):
    pass


@dataclass
class ValidationResult:
    ok: bool
    reason: str = ""
    imported_modules: list[str] = field(default_factory=list)


class ASTValidator(ast.NodeVisitor):
    def __init__(self):
        self.errors: list[str] = []
        self.modules: list[str] = []

    def visit_Import(self, node: ast.Import):  # noqa: N802
        for alias in node.names:
            root = alias.name.split(".")[0]
            if root not in ALLOWED_MODULES:
                self.errors.append(f"import of '{alias.name}' is not allowed")
            else:
                self.modules.append(root)

    def visit_ImportFrom(self, node: ast.ImportFrom):  # noqa: N802
        root = (node.module or "").split(".")[0]
        if root not in ALLOWED_MODULES:
            self.errors.append(f"import from '{node.module}' is not allowed")
        else:
            self.modules.append(root)

    def visit_Attribute(self, node: ast.Attribute):  # noqa: N802
        if node.attr.startswith("__"):
            self.errors.append(f"access to dunder attribute '{node.attr}' is not allowed")
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name):  # noqa: N802
        if node.id in FORBIDDEN_NAMES:
            self.errors.append(f"use of '{node.id}' is not allowed")
        self.generic_visit(node)

    def visit_With(self, node: ast.With):  # noqa: N802
        self.errors.append("'with' blocks are not allowed")

    def visit_AsyncWith(self, node: ast.AsyncWith):  # noqa: N802
        self.errors.append("'async with' blocks are not allowed")

    def visit_Global(self, node: ast.Global):  # noqa: N802
        self.errors.append("'global' is not allowed")

    def visit_Nonlocal(self, node: ast.Nonlocal):  # noqa: N802
        self.errors.append("'nonlocal' is not allowed")


def validate_program(program: str) -> ValidationResult:
    if not program or not program.strip():
        return ValidationResult(False, "empty program")
    if len(program) > 20000:
        return ValidationResult(False, "program is too large")
    try:
        tree = ast.parse(program)
    except SyntaxError as exc:
        return ValidationResult(False, f"syntax error: {exc}")
    validator = ASTValidator()
    validator.visit(tree)
    if validator.errors:
        return ValidationResult(False, "; ".join(validator.errors[:5]), validator.modules)
    if not any(isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "output" for t in node.targets) for node in ast.walk(tree)):
        return ValidationResult(False, "program must assign a variable named 'output'", validator.modules)
    return ValidationResult(True, "", validator.modules)


def _limit_resources(memory_mb: int, cpu_seconds: int) -> None:
    try:
        resource.setrlimit(resource.RLIMIT_AS, (memory_mb * 1024 * 1024, memory_mb * 1024 * 1024))
    except (ValueError, OSError):
        pass
    try:
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds + 2))
    except (ValueError, OSError):
        pass
    try:
        resource.setrlimit(resource.RLIMIT_NPROC, (0, 0))
    except (ValueError, OSError):
        pass


def run_program(program: str, inputs: dict[str, Any], *, timeout: float = 10.0, memory_mb: int = 512) -> tuple[bool, Any, str]:
    result = validate_program(program)
    if not result.ok:
        return False, None, result.reason
    payload = json.dumps({"program": program, "inputs": inputs, "modules": list(ALLOWED_MODULES)})
    import functools

    try:
        process = subprocess.run(
            [sys.executable, "-I", "-S", "-c", RUNNER],
            input=payload,
            capture_output=True,
            text=True,
            timeout=timeout,
            preexec_fn=functools.partial(_limit_resources, memory_mb, int(timeout)),
            cwd="/tmp",
            env={},
        )
    except subprocess.TimeoutExpired:
        return False, None, "computation exceeded the wall-clock limit"
    if process.returncode != 0:
        return False, None, f"program error: {(process.stderr or '')[-400:]}"
    try:
        parsed = json.loads(process.stdout)
    except json.JSONDecodeError:
        return False, None, "program did not produce JSON output"
    if not parsed.get("ok"):
        return False, None, str(parsed.get("error", "unknown error"))
    return True, parsed.get("output"), ""


SYSTEM_PROMPT = """You write a small Python program for bounded baseball artifact post-processing.

The program has access to:
- `inputs`: a dict mapping binding name -> list of row dicts (already computed by the database).
- allowed stdlib imports: math, statistics, datetime, collections, itertools, functools, json, re, decimal.
- a restricted builtin set (no open/eval/exec/__import__/getattr).

The program MUST assign a variable named `output`. `output` should be a list of row dicts
(or a dict) containing the computed result. Use only the numbers present in `inputs`.

Output JSON: {"program": "python source here", "output_description": "..."}
Do not include markdown fences inside the program. Keep it short and deterministic.
"""


class PythonComputeTool:
    def __init__(self, llm=None, *, timeout: float = 10.0, memory_mb: int = 512):
        self.llm = llm
        self.timeout = timeout
        self.memory_mb = memory_mb
        self.spec = CapabilitySpec(
            name="python_compute",
            description="Bounded Python post-processing over explicit Artifact bindings (alignment, differences, tests, ranking).",
            accepts=["STATISTICAL_RESULT", "RANKED_ENTITY_SET", "DERIVED_MEASURE"],
            produces=["DERIVED_MEASURE", "STATISTICAL_RESULT"],
            # Any one of the accepted data exports is enough; the tool itself fails closed
            # with INPUT_UNRESOLVED when no data row binding is present.
            required_inputs=[],
        )

    def execute(self, request: ToolRequest, ctx=None) -> CapabilityResult:
        inputs: dict[str, Any] = {}
        # Binding name -> artifact/export. Recorded so the Judge can independently rebuild
        # the factual operand values instead of trusting the untrusted program text.
        input_names: dict[str, dict[str, str]] = {}
        input_artifacts: list[Artifact] = []
        for binding in request.bindings:
            export = ctx.find_export(binding.export_id) if ctx else None
            if export is None:
                continue
            artifact = ctx.artifact_for_export(binding.export_id) if ctx else None
            if artifact is not None:
                input_artifacts.append(artifact)
            base = binding.source_need_id or binding.requirement or f"input_{len(inputs) + 1}"
            name = base
            suffix = 2
            while name in inputs:
                name = f"{base}_{suffix}"
                suffix += 1
            payload = export.payload or {}
            rows = payload.get("rows", payload)
            inputs[name] = rows
            if artifact is not None:
                input_names[name] = {"artifact_id": artifact.id, "export_id": binding.export_id}
            # Friendly alias so generated programs can use the season when it is obvious.
            for row in rows if isinstance(rows, list) else []:
                if isinstance(row, dict) and row.get("season") is not None:
                    alias = f"season_{row['season']}"
                    inputs.setdefault(alias, rows)
                    if artifact is not None:
                        input_names.setdefault(alias, {"artifact_id": artifact.id, "export_id": binding.export_id})
                    break
        for name, value in (request.inputs.get("inputs") or {}).items():
            inputs[name] = value

        if not inputs:
            return CapabilityResult(
                None,
                ToolOutcome(status="BLOCKED", code="INPUT_UNRESOLVED", message="python_compute needs bound artifact rows", need_id=request.need_id, tool_request_id=request.id, classification="WRONG_BINDING"),
            )

        # F5 (ADR 0020): a global rank/difference/extremum is checked with the declared
        # operation, not merely per-input completeness. Individually complete inputs can
        # still be incompatible (overlapping sources, different populations/qualifications,
        # unproven identity), and different seasons/sources can be legal partitions.
        operation = _operation_from_request(request)
        global_comparison = operation.kind != "LOCAL" or bool(request.inputs.get("global_comparison"))
        if input_artifacts:
            from app.capabilities.analytics.conformance import receipt_usable
            invalid = [a.id for a in input_artifacts if a.kind == "DATABASE_RESULT" and "execution_receipt" in a.structured_data and not receipt_usable(a)]
            if invalid:
                return CapabilityResult(None, ToolOutcome(status="BLOCKED", code="ANALYTICAL_NONCONFORMANCE", message=f"unusable analytical input receipts: {invalid}", need_id=request.need_id, tool_request_id=request.id, classification="SCOPE_MISMATCH"))

        from app.contracts.operations import GovernedOperation
        from app.capabilities.governed_compute import propose_bound_operations, execute_operations
        program = request.inputs.get("program")
        candidate_program_ignored = False
        bound_semantic_program = False
        governed = []
        try:
            if request.inputs.get('governed_operations'):
                governed = [GovernedOperation.model_validate(op) for op in request.inputs['governed_operations']]
            elif ctx and getattr(ctx, 'goal', None):
                governed = propose_bound_operations(ctx.goal, input_artifacts, context_artifacts=ctx.artifacts)
                if governed:
                    # The Goal owns the finite computation. A Planner-supplied Python
                    # candidate does not override that contract or select its output.
                    candidate_program_ignored = bool(program)
                    bound_semantic_program = True
                    program = None
        except (ValueError, TypeError):
            return CapabilityResult(None, ToolOutcome(status='FAILED', code='INVALID_IR',
                message='malformed governed operation', need_id=request.need_id, tool_request_id=request.id))
        # Exact finite contracts already declare every operand's scope and operation.
        # A legacy objective-word classifier cannot veto them as an unknown LOCAL merge.
        # Keep the legacy compatibility gate for all non-bound programs/proposals; final
        # factual authority still requires the Judge's unchanged recursive admission.
        if input_artifacts and not bound_semantic_program:
            compatibility = check_compatibility(operation, input_artifacts)
            if not compatibility.ok:
                return CapabilityResult(None, ToolOutcome(status='BLOCKED', code=compatibility.code,
                    message=compatibility.detail, need_id=request.need_id, tool_request_id=request.id,
                    classification='INCOMPATIBLE_ENUMERATION'))
        if governed and not program:
            try:
                output = execute_operations(governed, input_artifacts)
            except (ValueError, KeyError, IndexError, TypeError, ZeroDivisionError, OverflowError):
                return CapabilityResult(None, ToolOutcome(status='FAILED', code='INVALID_IR',
                    message='governed operation could not be evaluated', need_id=request.need_id, tool_request_id=request.id))
        elif not program:
            program = self._generate_program(request, inputs)
        if not program and not governed:
            return CapabilityResult(
                None,
                ToolOutcome(status="FAILED", code="INVALID_IR", message="no computation program could be produced", retryable=True, need_id=request.need_id, tool_request_id=request.id, classification="UNSUPPORTED_ANALYSIS"),
            )

        ok, error = True, ''
        if program:
            ok, output, error = run_program(program, inputs, timeout=self.timeout, memory_mb=self.memory_mb)
        if not ok:
            validation = validate_program(program)
            code = "COMPUTE_REJECTED" if not validation.ok else "COMPUTE_FAILED"
            return CapabilityResult(
                None,
                ToolOutcome(status="FAILED", code=code, message=error, need_id=request.need_id, tool_request_id=request.id, classification="UNSUPPORTED_ANALYSIS"),
            )

        rows = output if isinstance(output, list) else [output] if isinstance(output, dict) else []
        columns = list(rows[0].keys()) if rows and isinstance(rows[0], dict) else []
        candidate_input_ids = [a.id for a in input_artifacts]
        if bound_semantic_program:
            # Alternative scalar receipts were inspected, not added as operands.
            # Declare the exact selected ancestry that Judge will independently replay.
            used = {cell.artifact_id for op in governed for cell in op.operands}
            input_artifacts = list({a.id: a for a in input_artifacts if a.id in used}.values())
        enumeration = _derived_enumeration(input_artifacts, rows, operation)
        source_scopes = _source_scopes(input_artifacts)
        artifact = Artifact(
            kind="DERIVED_MEASURE",
            structured_data={
                "rows": rows,
                "governed_operations": [op.model_dump() for op in governed],
                "source_coverage_complete": all(a.status == "ACCEPTED" for a in input_artifacts),
                "input_artifact_ids": [a.id for a in input_artifacts],
                "candidate_input_artifact_ids": candidate_input_ids,
                "row_count": len(rows),
                "columns": columns,
                "input_names": input_names,
                "program_hash": abs(hash(program)) % 10**12 if program else None,
                "inputs": {name: len(value) if isinstance(value, list) else 1 for name, value in inputs.items()},
                "objective": request.objective,
                "global_comparison": global_comparison,
                "operation": operation.model_dump(),
                "enumeration": enumeration.model_dump(),
                "source_scopes": source_scopes,
            },
            text_content=_render(rows, columns),
            exports=[
                Export(
                    id=f"exp_{request.id}_derived",
                    type="DERIVED_MEASURE",
                    payload={"rows": rows, "columns": columns},
                    contract={"grain": "DERIVED", "producer": "python_compute"},
                )
            ],
            provenance={"capability": "python_compute", "sandbox": "ast+subprocess+rlimit" if program else "trusted-finite-ir",
                        "request_id": request.id, "program": program, "candidate_program_ignored": candidate_program_ignored},
            references=[Reference(ref_type=ReferenceType.DERIVED_COMPUTATION, ref_id=request.id, source="python_compute", span=_render(rows, columns)[:500])],
            requested_scope=Scope(measure=request.objective),
            actual_scope=Scope(measure=request.objective, source="PYTHON_COMPUTE"),
            enumeration=enumeration,
            lineage=[f"artifact:{a.id}" for a in input_artifacts],
            confidence="ACCEPTABLE" if rows else "WEAK",
            status="ACCEPTED" if rows and all(a.status == "ACCEPTED" for a in input_artifacts) else "PARTIAL",
            need_id=request.need_id,
        )
        return CapabilityResult(
            artifact,
            ToolOutcome(status="SUCCESS" if rows else "NO_DATA", code="OK" if rows else "VALID_EMPTY", message=f"computed {len(rows)} row(s)", need_id=request.need_id, tool_request_id=request.id),
        )

    def _generate_program(self, request: ToolRequest, inputs: dict[str, Any]) -> str | None:
        if not self.llm or not self.llm.configured:
            return None
        schemas = {
            name: (sorted(value[0].keys()) if isinstance(value, list) and value and isinstance(value[0], dict) else [])
            for name, value in inputs.items()
        }
        user = json.dumps(
            {
                "objective": request.objective,
                "inputs_schema": schemas,
                "sample_rows": {name: (value[:3] if isinstance(value, list) else value) for name, value in inputs.items()},
            },
            ensure_ascii=False,
            default=str,
        )
        try:
            result = self.llm.complete_json(role="analytics", system=SYSTEM_PROMPT, user=user, max_tokens=1200)
        except Exception:  # noqa: BLE001
            return None
        program = result.data.get("program")
        return program if isinstance(program, str) else None


def _render(rows: list[dict[str, Any]], columns: list[str]) -> str:
    if not rows:
        return "The computation returned no rows."
    header = " | ".join(columns)
    body = "\n".join(" | ".join(str(row.get(column, "")) for column in columns) for row in rows[:25])
    return f"{header}\n{body}"


def _operation_from_request(request: ToolRequest) -> GlobalOperation:
    raw = request.inputs.get("operation")
    if isinstance(raw, dict):
        try:
            return GlobalOperation.model_validate(raw)
        except Exception:  # noqa: BLE001 - a malformed operation must fail closed
            return GlobalOperation(kind="UNKNOWN")
    if request.inputs.get("global_comparison"):
        # Legacy flag with no structured semantics: fail closed for multi-input work.
        return GlobalOperation(kind="UNKNOWN")
    return GlobalOperation(kind="LOCAL")


def _derived_enumeration(
    input_artifacts: list[Artifact], rows: list[dict[str, Any]], operation: GlobalOperation | None = None
) -> EnumerationContract:
    contracts = [artifact.enumeration for artifact in input_artifacts if artifact.enumeration is not None]
    if not contracts:
        return EnumerationContract(
            returned_rows=len(rows),
            completeness="UNKNOWN",
            source="PYTHON_COMPUTE",
            note="no enumeration contract on inputs",
        )
    if any(contract.completeness == "TRUNCATED" for contract in contracts):
        completeness = "TRUNCATED"
    elif any(contract.completeness == "UNKNOWN" for contract in contracts):
        completeness = "UNKNOWN"
    else:
        completeness = "COMPLETE"
    populations = {contract.population for contract in contracts if contract.population}
    qualifications = {contract.qualification for contract in contracts if contract.qualification}
    kinds = {contract.entity_kind for contract in contracts if contract.entity_kind}
    namespaces = {contract.entity_namespace for contract in contracts if contract.entity_namespace}
    sources = sorted({contract.source for contract in contracts if contract.source})
    operation_kind = operation.kind if operation is not None else "UNKNOWN"
    return EnumerationContract(
        population=next(iter(populations)) if len(populations) == 1 else None,
        qualification=next(iter(qualifications)) if len(qualifications) == 1 else None,
        ordering=sorted({order for contract in contracts for order in contract.ordering}),
        ordering_verified=False,
        returned_rows=len(rows),
        completeness=completeness,
        entity_kind=next(iter(kinds)) if len(kinds) == 1 else None,
        entity_namespace=next(iter(namespaces)) if len(namespaces) == 1 else None,
        source="PYTHON_COMPUTE",
        source_coverage=",".join(sources) or None,
        note=f"derived from {len(contracts)} compatible input population(s) via {operation_kind}",
    )


def _source_scopes(input_artifacts: list[Artifact]) -> list[dict[str, Any]]:
    scopes: list[dict[str, Any]] = []
    for artifact in input_artifacts:
        enumeration = artifact.enumeration
        scope = artifact.actual_scope
        scopes.append(
            {
                "artifact_id": artifact.id,
                "season": enumeration.season if enumeration else (scope.season if scope else None),
                "population": enumeration.population if enumeration else (scope.game_population if scope else None),
                "date_range": (
                    enumeration.date_range.model_dump()
                    if enumeration and enumeration.date_range
                    else (scope.date_range.model_dump() if scope and scope.date_range else None)
                ),
                "measures": list(scope.measures) if scope else [],
                "qualification": enumeration.qualification if enumeration else None,
                "completeness": enumeration.completeness if enumeration else "UNKNOWN",
                "entity_kind": enumeration.entity_kind if enumeration else None,
                "entity_namespace": enumeration.entity_namespace if enumeration else None,
                "population_count": enumeration.population_count if enumeration else None,
                "source": enumeration.source if enumeration else (scope.source if scope else None),
                "source_coverage": enumeration.source_coverage if enumeration else None,
            }
        )
    return scopes
