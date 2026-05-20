from __future__ import annotations

from pathlib import Path

import typer
from pydantic import ValidationError

from perago.config import load_runtime_config
from perago.errors import RuntimeConfigError, TaskDefinitionError
from perago.task import load_module_task
from perago.taskdef import write_taskdef


app = typer.Typer(no_args_is_help=True)


@app.command()
def check(module_target: str) -> None:
    """Validate one Perago task module and local runtime config."""
    try:
        task = load_module_task(module_target)
        config = load_runtime_config(module_target)
    except (TaskDefinitionError, RuntimeConfigError, ValidationError) as exc:
        _fail(str(exc))
    typer.echo(f"ok: {task.name}")
    typer.echo(f"workspace_root: {config.workspace_root}")
    typer.echo(f"log_root: {config.log_root}")
    typer.echo(f"worker_id_prefix: {config.worker_id_prefix}")


@app.command()
def extract(module_target: str, out: Path) -> None:
    """Write generated Conductor TaskDef JSON for one task module."""
    try:
        task = load_module_task(module_target)
        load_runtime_config(module_target)
        path = write_taskdef(task, out)
    except (TaskDefinitionError, RuntimeConfigError, ValidationError) as exc:
        _fail(str(exc))
    typer.echo(str(path))


@app.command()
def start(module_target: str, j: int = typer.Option(1, "-j", min=1)) -> None:
    """Validate startup inputs; worker polling is implemented with service integration."""
    try:
        load_module_task(module_target)
        load_runtime_config(module_target)
    except (TaskDefinitionError, RuntimeConfigError, ValidationError) as exc:
        _fail(str(exc))
    _fail(
        "perago start is reserved for the Conductor/LakeFS worker integration phase; "
        f"validated module={module_target} worker_processes={j}"
    )


def _fail(message: str) -> None:
    typer.echo(f"error: {message}", err=True)
    raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
