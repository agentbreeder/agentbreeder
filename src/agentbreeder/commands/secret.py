"""`agentbreeder secret set`: manage local secret files (secret:// refs)."""

from __future__ import annotations

import sys
from pathlib import Path

from agentbreeder import cli, env_import, secret_files

NAMES = ("secret",)


def register(commands) -> None:
    secret = commands.add_parser("secret", help="Manage local secret files (secret:// refs)")
    secret_commands = secret.add_subparsers(dest="secret_command", required=True)
    secret_set = secret_commands.add_parser(
        "set",
        help="Store a secret value from stdin, an environment variable or a generated token "
        "(never from arguments)",
    )
    secret_set.add_argument("name")
    secret_set.add_argument("--state-dir", type=Path, default=cli.DEFAULT_STATE)
    source = secret_set.add_mutually_exclusive_group()
    source.add_argument(
        "--from-env", metavar="VAR", help="Read the value from environment variable VAR"
    )
    source.add_argument("--generate", action="store_true", help="Store a random hex token")
    secret_set.add_argument(
        "--env-file", type=Path, help="With --from-env: also look in this KEY=value file"
    )
    secret_set.add_argument(
        "--force", action="store_true", help="With --generate: replace an existing secret"
    )


def run(args) -> int:
    if args.env_file and not args.from_env:
        cli._parser().error("--env-file needs --from-env")
    if args.force and not args.generate:
        cli._parser().error("--force needs --generate")
    try:
        if args.from_env:
            value = env_import.lookup(args.from_env, args.env_file)
            if value is None:
                print(
                    f"[SECRET_SOURCE_MISSING] {args.from_env} is not set in the environment"
                    + (f" or {args.env_file}" if args.env_file else ""),
                    file=sys.stderr,
                )
                return 2
            data, source = value.encode(), f"from {args.from_env}"
        elif args.generate:
            if secret_files.exists(args.state_dir, args.name) and not args.force:
                print(
                    f"[SECRET_EXISTS] Secret {args.name} already exists; "
                    "pass --force to replace it",
                    file=sys.stderr,
                )
                return 2
            data, source = secret_files.generate_token(), "generated"
        else:
            data = sys.stdin.buffer.read(secret_files.MAX_BYTES + 2)
            if data.endswith(b"\n"):
                data = data[:-1]  # `echo value |` and here-strings add one newline
            source = "from stdin"
        path = secret_files.set_secret(args.state_dir, args.name, data)
    except (secret_files.SecretError, env_import.EnvImportError) as error:
        print(f"[{error.code}] {error}", file=sys.stderr)
        return 2
    print(
        f"Stored secret {args.name} ({source}) at {path} (0600). "
        f"Reference it as secret://{args.name}"
    )
    return 0
