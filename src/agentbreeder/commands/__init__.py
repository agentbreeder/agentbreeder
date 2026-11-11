"""One module per CLI command (or small family of commands).

Each module defines `NAMES` (the subcommands it adds), `register(commands)` (adds its parsers to
the top-level subparsers, in the order `agentbreeder --help` lists them) and `run(args)`.
`agentbreeder.cli.COMMAND_MODULES` lists the modules in registration order.
"""
