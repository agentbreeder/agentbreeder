"""Agent Garden CLI — the developer interface.

Usage:
    garden deploy ./agent.yaml --target local
    garden validate ./agent.yaml
    garden list agents
    garden describe agent <name>
"""

from __future__ import annotations

import typer

from cli.commands import (
    chat,
    deploy,
    describe,
    init_cmd,
    list_cmd,
    logs,
    provider,
    publish,
    review,
    scan,
    search,
    status,
    submit,
    teardown,
    validate,
)

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
app.command(name="submit")(submit.submit)
app.command(name="publish")(publish.publish)
app.command(name="chat")(chat.chat)
app.add_typer(provider.provider_app, name="provider")
app.add_typer(review.review_app, name="review")


if __name__ == "__main__":
    app()
