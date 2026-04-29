"""AgentBreeder CLI — the developer interface.

Usage:
    agentbreeder deploy ./agent.yaml --target local
    agentbreeder validate ./agent.yaml
    agentbreeder list agents
    agentbreeder describe agent <name>
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version

import typer


def _version_callback(value: bool) -> None:
    if value:
        try:
            v = _pkg_version("agentbreeder")
        except PackageNotFoundError:
            v = "dev"
        typer.echo(f"agentbreeder {v}")
        raise typer.Exit()


from cli.commands import (
    chat,
    compliance,
    deploy,
    describe,
    down,
    init_cmd,
    list_cmd,
    logs,
    model,
    orchestration,
    provider,
    publish,
    quickstart,
    registry_cmd,
    review,
    scan,
    schedule,
    search,
    secret,
    seed,
    setup,
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


def _main_callback(
    version: bool = typer.Option(
        False,
        "--version",
        "-V",
        callback=_version_callback,
        is_eager=True,
        help="Show version and exit.",
    ),
) -> None:
    pass


app = typer.Typer(
    name="agentbreeder",
    help="AgentBreeder — Define Once. Deploy Anywhere. Govern Automatically.",
    no_args_is_help=True,
    rich_markup_mode="rich",
    callback=_main_callback,
)

app.command(name="quickstart")(quickstart.quickstart)
app.command(name="seed")(seed.seed)
app.command(name="setup")(setup.setup)
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
app.command(name="schedule")(schedule.schedule)
app.command(name="logs")(logs.logs)
app.command(name="status")(status.status)
app.command(name="teardown")(teardown.teardown)
app.command(name="submit")(submit.submit)
app.command(name="publish")(publish.publish)
app.command(name="chat")(chat.chat)
app.add_typer(provider.provider_app, name="provider")
app.add_typer(model.model_app, name="model")
app.add_typer(review.review_app, name="review")
app.add_typer(eval_cmd.eval_app, name="eval")
app.add_typer(orchestration.orchestration_app, name="orchestration")
app.add_typer(template.template_app, name="template")
app.add_typer(secret.secret_app, name="secret")
app.add_typer(compliance.compliance_app, name="compliance")
app.add_typer(registry_cmd.registry_app, name="registry")


if __name__ == "__main__":
    app()
