"""`perf` typer app — entry point `perf.cli:main` (`pyproject.toml`
console_scripts). Global flags (SKILL rule 6): `--json`, `--no-color`
(+ `NO_COLOR` env + TTY detection), `--db`, `--config`.

Bare `perf` (no subcommand) and `perf --help` show the small ASCII banner
(roadmap #45), gated by TTY + `--json` + `--no-color`/`NO_COLOR`
(`perf.cli.banner`). `perf run --help` and any other subcommand's own
`--help` keep click's normal per-command help — the banner is NEVER part
of a command's own output.
"""

from __future__ import annotations

import sys

import typer

from perf.cli.banner import render_banner, should_show_banner
from perf.cli.commands.compare import compare as compare_command
from perf.cli.commands.run import run as run_command
from perf.cli.output.context import resolve_output_context
from perf.config.loader import load_config

app = typer.Typer(
    add_completion=False,
    invoke_without_command=True,
    no_args_is_help=False,
    context_settings={"help_option_names": []},
    help="Local-first performance lab CLI — run Maestro flows, capture "
    "markers + Flashlight samples, persist and compare against local "
    "history.",
)


def _print_help_with_banner(ctx: typer.Context, output) -> None:
    if should_show_banner(json_mode=output.json_mode, stdout_is_tty=output.stdout_is_tty):
        typer.echo(render_banner(color=output.color_enabled))
    typer.echo(ctx.get_help())


@app.callback(invoke_without_command=True)
def main_callback(
    ctx: typer.Context,
    help_: bool = typer.Option(
        False, "--help", "-h", help="Show this help message and exit.", is_eager=True
    ),
    json_output: bool = typer.Option(
        False, "--json", help="Emit the stable machine --json contract (schema_version=1)."
    ),
    no_color: bool = typer.Option(
        False, "--no-color", help="Disable ANSI color output (also honors NO_COLOR env)."
    ),
    db: str | None = typer.Option(
        None, "--db", help="Path to the local SQLite store (also honors PERF_DB env)."
    ),
    config: str | None = typer.Option(
        None, "--config", help="Path to a project perf.toml config file."
    ),
) -> None:
    # Load config FIRST so the resolved project/global `no_color` can feed the
    # output context (precedence: CLI flag > NO_COLOR env > config > TTY).
    perf_config = load_config(
        cli_db=db,
        cli_config_path=config,
        cli_no_color=no_color if no_color else None,
    )
    output = resolve_output_context(
        json_mode=json_output,
        no_color_cli=no_color,
        no_color_config=perf_config.no_color,
        stdout=sys.stdout,
    )
    ctx.obj = {"output": output, "config": perf_config}

    if help_:
        _print_help_with_banner(ctx, output)
        raise typer.Exit(code=0)

    if ctx.invoked_subcommand is None:
        _print_help_with_banner(ctx, output)
        raise typer.Exit(code=0)


app.command(
    name="run",
    context_settings={"help_option_names": ["--help", "-h"]},
)(run_command)

app.command(
    name="compare",
    context_settings={"help_option_names": ["--help", "-h"]},
)(compare_command)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
