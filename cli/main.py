"""AgentBreeder CLI — the developer interface.

Usage:
    agentbreeder deploy ./agent.yaml --target local
    agentbreeder validate ./agent.yaml
    agentbreeder list agents
    agentbreeder describe agent <name>
"""

from __future__ import annotations

import typer

from cli.commands import (
    chat,
    deploy,
    describe,
    down,
    init_cmd,
    list_cmd,
    logs,
    orchestration,
    provider,
    publish,
    review,
    scan,
    search,
    secret,
    status,
    submit,
    teardown,
    template,
    ui,
    up,
    validate,
)
from cli.commands import (
    eval as eval_cmd,
)

app = typer.Typer(
    name="agentbreeder",
    help="AgentBreeder — Define Once. Deploy Anywhere. Govern Automatically.",
    no_args_is_help=True,
    rich_markup_mode="rich",
)

app.command(name="ui")(ui.ui)
app.command(name="up")(up.up)
app.command(name="down")(down.down)
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
app.add_typer(eval_cmd.eval_app, name="eval")
app.add_typer(orchestration.orchestration_app, name="orchestration")
app.add_typer(template.template_app, name="template")
app.add_typer(secret.secret_app, name="secret")


if __name__ == "__main__":
    app()
