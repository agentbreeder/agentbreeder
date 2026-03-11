"""Agent Garden CLI — the developer interface.

Usage:
    garden deploy ./agent.yaml --target local
    garden validate ./agent.yaml
    garden list agents
    garden describe agent <name>
"""

from __future__ import annotations

import typer

from cli.commands import deploy, validate, list_cmd, describe, search, scan, init_cmd, logs, status, teardown, provider

app = typer.Typer(
    name="garden",
    help="Agent Garden — Define Once. Deploy Anywhere. Govern Automatically.",
    no_args_is_help=True,
    rich_markup_mode="rich",
)

app.command(name="init")(init_cmd.init)
app.command(name="deploy")(deploy.deploy)
app.command(name="validate")(validate.validate)
app.command(name="list")(list_cmd.list_entities)
app.command(name="describe")(describe.describe)
app.command(name="search")(search.search)
app.command(name="scan")(scan.scan)
app.command(name="logs")(logs.logs)
app.command(name="status")(status.status)
app.command(name="teardown")(teardown.teardown)
app.add_typer(provider.provider_app, name="provider")


if __name__ == "__main__":
    app()
