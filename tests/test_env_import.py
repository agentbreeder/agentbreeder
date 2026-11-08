import os

import pytest

from agentbreeder import env_import, secret_files
from agentbreeder.cli import main

CANARY = "canary-7f3a9c-not-a-real-key"


def _write(path, text, mode=0o600):
    path.write_text(text)
    path.chmod(mode)
    return path


def test_parse_accepts_plain_export_quotes_comments(tmp_path):
    env = _write(
        tmp_path / ".env",
        "# provider keys\n"
        "\n"
        "GEMINI_API_KEY=abc123\n"
        "export TAVILY_API_KEY=tv-1\n"
        'QUOTED="two words"\n'
        "SINGLE='a=b#c'\n"
        "SPACED = spaced\n"
        "EMPTY=\n"
        "TRAILING=value # comment\n"
        "GEMINI_API_KEY=later-wins\n",
    )
    values = env_import.parse_env_file(env)
    assert values == {
        "GEMINI_API_KEY": "later-wins",
        "TAVILY_API_KEY": "tv-1",
        "QUOTED": "two words",
        "SINGLE": "a=b#c",
        "SPACED": "spaced",
        "EMPTY": "",
        "TRAILING": "value",
    }


@pytest.mark.parametrize(
    "line",
    [
        "not an assignment",
        "1BAD=x",
        "BAD-NAME=x",
        'UNCLOSED="abc',
        "CMD=$(whoami)",
        "VAR=${HOME}",
        "TICKS=`id`",
    ],
)
def test_parse_rejects_bad_lines_without_echoing_them(tmp_path, line):
    env = _write(tmp_path / ".env", f"OK=1\n{line}\n")
    with pytest.raises(env_import.EnvImportError) as caught:
        env_import.parse_env_file(env)
    assert caught.value.code == "ENV_FILE_INVALID"
    assert "line 2" in str(caught.value)
    assert line not in str(caught.value)


def test_parse_refuses_symlink_oversize_and_foreign_modes(tmp_path):
    real = _write(tmp_path / "real.env", "A=1\n")
    link = tmp_path / "link.env"
    link.symlink_to(real)
    with pytest.raises(env_import.EnvImportError) as caught:
        env_import.parse_env_file(link)
    assert caught.value.code == "ENV_FILE_UNSAFE"

    big = _write(tmp_path / "big.env", "A=" + "x" * (env_import.MAX_BYTES + 1) + "\n")
    with pytest.raises(env_import.EnvImportError) as caught:
        env_import.parse_env_file(big)
    assert caught.value.code == "ENV_FILE_UNSAFE"

    with pytest.raises(env_import.EnvImportError) as caught:
        env_import.parse_env_file(tmp_path / "missing.env")
    assert caught.value.code == "ENV_FILE_UNSAFE"

    shared = _write(tmp_path / "shared.env", "A=1\n", mode=0o666)
    with pytest.raises(env_import.EnvImportError) as caught:
        env_import.parse_env_file(shared)
    assert caught.value.code == "ENV_FILE_UNSAFE"


@pytest.mark.parametrize(
    "line",
    ['A="quoted" trailing', 'A="$(id)"', 'A="${HOME}"'],
)
def test_parse_rejects_quoted_edge_cases(tmp_path, line):
    env = _write(tmp_path / ".env", line + "\n")
    with pytest.raises(env_import.EnvImportError) as caught:
        env_import.parse_env_file(env)
    assert caught.value.code == "ENV_FILE_INVALID"


def test_parse_allows_quoted_comment_and_single_quoted_dollar(tmp_path):
    env = _write(tmp_path / ".env", "A=\"x\" # note\nB='$(not expanded)'\n")
    assert env_import.parse_env_file(env) == {"A": "x", "B": "$(not expanded)"}


def test_parse_rejects_directories_and_non_utf8(tmp_path):
    folder = tmp_path / "dir.env"
    folder.mkdir()
    with pytest.raises(env_import.EnvImportError) as caught:
        env_import.parse_env_file(folder)
    assert caught.value.code == "ENV_FILE_UNSAFE"
    binary = tmp_path / "bin.env"
    binary.write_bytes(b"A=\xff\xfe\n")
    binary.chmod(0o600)
    with pytest.raises(env_import.EnvImportError) as caught:
        env_import.parse_env_file(binary)
    assert caught.value.code == "ENV_FILE_INVALID"


def test_cli_force_needs_generate(tmp_path):
    with pytest.raises(SystemExit):
        main(["secret", "set", "x", "--force", "--state-dir", str(tmp_path)])


@pytest.mark.parametrize(
    "name",
    [
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
        "AZURE_CLIENT_SECRET",
        "GOOGLE_APPLICATION_CREDENTIALS",
        "GITHUB_TOKEN",
        "GH_TOKEN",
        "NPM_TOKEN",
        "PYPI_API_TOKEN",
        "SSH_AUTH_SOCK",
        "GIT_SSH_COMMAND",
        "KUBECONFIG",
        "VAULT_TOKEN",
        "DOCKER_PASSWORD",
    ],
)
def test_denylisted_names_are_refused(name, monkeypatch):
    monkeypatch.setenv(name, CANARY)
    with pytest.raises(env_import.EnvImportError) as caught:
        env_import.lookup(name)
    assert caught.value.code == "IMPORT_DENIED"
    assert CANARY not in str(caught.value)


def test_lookup_prefers_environment_then_file(tmp_path, monkeypatch):
    env = _write(tmp_path / ".env", "GEMINI_API_KEY=from-file\nTAVILY_API_KEY=tv\n")
    monkeypatch.setenv("GEMINI_API_KEY", "from-env")
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    assert env_import.lookup("GEMINI_API_KEY", env) == "from-env"
    assert env_import.lookup("TAVILY_API_KEY", env) == "tv"
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    assert env_import.lookup("OPENROUTER_API_KEY", env) is None
    assert env_import.lookup("OPENROUTER_API_KEY") is None


def test_lookup_rejects_bad_variable_names():
    with pytest.raises(env_import.EnvImportError) as caught:
        env_import.lookup("lower-case")
    assert caught.value.code == "IMPORT_NAME"


def test_generate_token_is_hex_and_unique():
    first, second = secret_files.generate_token(), secret_files.generate_token()
    assert first != second
    assert len(first) == 48 and all(c in b"0123456789abcdef" for c in first)
    assert len(secret_files.generate_token(8)) == 16


def test_cli_from_env_stores_value_without_printing_it(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("GEMINI_API_KEY", CANARY)
    state = tmp_path / "state"
    assert (
        main(
            ["secret", "set", "gemini", "--from-env", "GEMINI_API_KEY", "--state-dir", str(state)]
        )
        == 0
    )
    assert (state / "secrets" / "gemini").read_text() == CANARY
    out = capsys.readouterr()
    assert CANARY not in out.out + out.err
    assert "GEMINI_API_KEY" in out.out


def test_cli_from_env_file(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    env = _write(tmp_path / ".env", f"TAVILY_API_KEY={CANARY}\n")
    state = tmp_path / "state"
    assert (
        main(
            [
                "secret",
                "set",
                "tavily",
                "--from-env",
                "TAVILY_API_KEY",
                "--env-file",
                str(env),
                "--state-dir",
                str(state),
            ]
        )
        == 0
    )
    assert (state / "secrets" / "tavily").read_text() == CANARY
    assert CANARY not in capsys.readouterr().out


def test_cli_from_env_missing_and_denied(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("NOPE_KEY", raising=False)
    state = tmp_path / "state"
    assert main(["secret", "set", "x", "--from-env", "NOPE_KEY", "--state-dir", str(state)]) == 2
    err = capsys.readouterr().err
    assert "SECRET_SOURCE_MISSING" in err and "NOPE_KEY" in err

    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", CANARY)
    assert (
        main(
            [
                "secret",
                "set",
                "x",
                "--from-env",
                "AWS_SECRET_ACCESS_KEY",
                "--state-dir",
                str(state),
            ]
        )
        == 2
    )
    out = capsys.readouterr()
    assert "IMPORT_DENIED" in out.err and CANARY not in out.out + out.err
    assert not (state / "secrets" / "x").exists()


def test_cli_from_env_empty_value_refused(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("EMPTY_KEY", "")
    assert (
        main(["secret", "set", "x", "--from-env", "EMPTY_KEY", "--state-dir", str(tmp_path / "s")])
        == 2
    )
    assert "SECRET_EMPTY" in capsys.readouterr().err


def test_cli_env_file_requires_from_env(tmp_path, capsys):
    env = _write(tmp_path / ".env", "A=1\n")
    with pytest.raises(SystemExit):
        main(["secret", "set", "x", "--env-file", str(env), "--state-dir", str(tmp_path)])


def test_cli_bad_env_file_reported(tmp_path, capsys):
    env = _write(tmp_path / ".env", "CMD=$(id)\n")
    assert (
        main(
            [
                "secret",
                "set",
                "x",
                "--from-env",
                "CMD",
                "--env-file",
                str(env),
                "--state-dir",
                str(tmp_path / "s"),
            ]
        )
        == 2
    )
    assert "ENV_FILE_INVALID" in capsys.readouterr().err


def test_cli_generate_and_no_silent_overwrite(tmp_path, capsys):
    state = tmp_path / "state"
    assert main(["secret", "set", "master", "--generate", "--state-dir", str(state)]) == 0
    first = (state / "secrets" / "master").read_bytes()
    assert len(first) == 48
    assert first.decode() not in capsys.readouterr().out

    assert main(["secret", "set", "master", "--generate", "--state-dir", str(state)]) == 2
    assert "SECRET_EXISTS" in capsys.readouterr().err
    assert (state / "secrets" / "master").read_bytes() == first

    assert (
        main(["secret", "set", "master", "--generate", "--force", "--state-dir", str(state)]) == 0
    )
    assert (state / "secrets" / "master").read_bytes() != first
    assert oct(os.stat(state / "secrets" / "master").st_mode & 0o777) == "0o600"


def test_cli_sources_are_mutually_exclusive(tmp_path):
    with pytest.raises(SystemExit):
        main(["secret", "set", "x", "--generate", "--from-env", "A", "--state-dir", str(tmp_path)])
