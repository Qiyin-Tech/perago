from __future__ import annotations

from pathlib import Path

import typer
from pydantic import ValidationError
from pydantic.errors import PydanticInvalidForJsonSchema

from perago._version import __version__
from perago.conductor_runtime import OrkesConductorRuntimeClient
from perago.config import ExecutionMode, load_runtime_config
from perago.errors import RuntimeConfigError, TaskDefinitionError
from perago.supervisor import run_worker_supervisor
from perago.task import load_module_task
from perago.taskdef import build_taskdef, write_taskdef


app = typer.Typer(no_args_is_help=True)


def _version_callback(value: bool) -> None:
    if not value:
        return
    typer.echo(__version__)
    raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(False, "--version", is_eager=True, callback=_version_callback),
) -> None:
    """Perago CLI."""


@app.command()
def check(module_target: str) -> None:
    """Validate one Perago task module and local runtime config."""
    try:
        config = load_runtime_config(module_target)
        task = load_module_task(module_target)
        build_taskdef(task)
    except (TaskDefinitionError, RuntimeConfigError, ValidationError, PydanticInvalidForJsonSchema) as exc:
        _fail(str(exc))
    typer.echo(f"ok: {task.name}")
    typer.echo(f"workspace_root: {config.workspace_root}")
    typer.echo(f"log_root: {config.log_root}")
    typer.echo(f"worker_id_prefix: {config.worker_id_prefix}")
    typer.echo(f"conductor: {_configured(config.conductor is not None)}")
    typer.echo(f"lakefs: {_configured(config.lakefs is not None)}")


@app.command()
def extract(module_target: str, output: Path = typer.Option(..., "--output", "-o")) -> None:
    """Write generated Conductor TaskDef JSON for one task module."""
    try:
        load_runtime_config(module_target)
        task = load_module_task(module_target)
        path = write_taskdef(task, output)
    except (TaskDefinitionError, RuntimeConfigError, ValidationError, PydanticInvalidForJsonSchema, ValueError) as exc:
        _fail(str(exc))
    typer.echo(str(path))


@app.command()
def start(
    module_target: str,
    j: int = typer.Option(1, "-j", min=1),
    execution_mode: ExecutionMode | None = typer.Option(None, "--execution-mode"),
) -> None:
    """Start Conductor worker processes for one Perago task module."""
    try:
        config = load_runtime_config(module_target)
        resolved_execution_mode = execution_mode or config.execution_mode
        if config.conductor is None:
            raise RuntimeConfigError("CONDUCTOR_SERVER_URL is required for perago start")
        if config.lakefs is None:
            raise RuntimeConfigError("LakeFS config is required for perago start")
        task = load_module_task(module_target)
        build_taskdef(task)
        conductor = OrkesConductorRuntimeClient.from_config(config.conductor)
        if not conductor.taskdef_exists(task.name):
            raise RuntimeConfigError(
                f"Conductor TaskDef {task.name!r} is not registered; run perago extract and register it before start"
            )
    except (TaskDefinitionError, RuntimeConfigError, ValidationError, PydanticInvalidForJsonSchema) as exc:
        _fail(str(exc))
    except Exception as exc:  # noqa: BLE001
        _fail(f"failed to validate Conductor TaskDef: {exc}")
    run_worker_supervisor(
        config=config,
        module_target=module_target,
        process_count=j,
        execution_mode=resolved_execution_mode,
    )


def _fail(message: str) -> None:
    typer.echo(f"error: {message}", err=True)
    raise typer.Exit(code=1)


def _configured(value: bool) -> str:
    return "configured" if value else "not configured"


if __name__ == "__main__":
    app()
