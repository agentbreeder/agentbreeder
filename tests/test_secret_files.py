"""Local secret files (#727): storage, preflight, copies for bind mounts, cleanup."""

from __future__ import annotations

import os

import pytest

from agentbreeder import secret_files
from agentbreeder.secret_files import SecretError

DEPLOYMENT = "a" * 32


def test_set_secret_is_private_and_atomic(tmp_path):
    path = secret_files.set_secret(tmp_path, "db-url", b"postgresql://x")
    assert path.read_bytes() == b"postgresql://x"
    assert path.stat().st_mode & 0o777 == 0o600
    assert (tmp_path / "secrets").stat().st_mode & 0o777 == 0o700
    secret_files.set_secret(tmp_path, "db-url", b"new")
    assert path.read_bytes() == b"new"
    assert not list((tmp_path / "secrets").glob(".*.tmp"))


@pytest.mark.parametrize("name", ["", "Upper", "a/b", "../x", "-lead", "x" * 64])
def test_bad_names_rejected(tmp_path, name):
    with pytest.raises(SecretError) as error:
        secret_files.set_secret(tmp_path, name, b"v")
    assert error.value.code == "SECRET_NAME"


@pytest.mark.parametrize("value", [b"", b"x" * (secret_files.MAX_BYTES + 1)])
def test_empty_or_huge_values_rejected(tmp_path, value):
    with pytest.raises(SecretError) as error:
        secret_files.set_secret(tmp_path, "k", value)
    assert error.value.code == "SECRET_EMPTY"


def test_reference_names_restricted():
    assert secret_files.secret_name("secret://northwind-db") == "northwind-db"
    with pytest.raises(SecretError):
        secret_files.secret_name("secret://team/key")


def test_preflight_missing_mode_and_symlink(tmp_path):
    with pytest.raises(SecretError) as error:
        secret_files.preflight(tmp_path, {"A": "secret://k"})
    assert error.value.code == "SECRET_MISSING"
    path = secret_files.set_secret(tmp_path, "k", b"v")
    path.chmod(0o644)
    with pytest.raises(SecretError) as error:
        secret_files.preflight(tmp_path, {"A": "secret://k"})
    assert error.value.code == "SECRET_MODE"
    path.chmod(0o600)
    secret_files.preflight(tmp_path, {"A": "secret://k"})
    target = tmp_path / "elsewhere"
    target.write_bytes(b"v")
    target.chmod(0o600)
    link = tmp_path / "secrets" / "link"
    os.symlink(target, link)
    with pytest.raises(SecretError) as error:
        secret_files.preflight(tmp_path, {"A": "secret://link"})
    assert error.value.code == "SECRET_MODE"


def test_materialise_copies_readable_files_in_private_dir_and_cleanup(tmp_path):
    secret_files.set_secret(tmp_path, "db", b"one")
    secret_files.set_secret(tmp_path, "key", b"two")
    mounts = secret_files.materialise(
        tmp_path, DEPLOYMENT, {"DATABASE_URL": "secret://db", "API_KEY": "secret://key"}
    )
    assert [(m.envs, m.target) for m in mounts] == [
        (("DATABASE_URL",), "/run/secrets/db"),
        (("API_KEY",), "/run/secrets/key"),
    ]
    directory = secret_files.deployment_dir(tmp_path, DEPLOYMENT)
    assert directory.stat().st_mode & 0o777 == 0o700
    assert all(m.source.stat().st_mode & 0o777 == 0o444 for m in mounts)
    assert mounts[0].source.read_bytes() == b"one"
    assert secret_files.present(tmp_path, DEPLOYMENT, {"A": "secret://db"})
    # re-materialising (retry) overwrites the read-only copies
    secret_files.set_secret(tmp_path, "db", b"rotated")
    mounts = secret_files.materialise(tmp_path, DEPLOYMENT, {"DATABASE_URL": "secret://db"})
    assert mounts[0].source.read_bytes() == b"rotated"
    secret_files.cleanup(tmp_path, DEPLOYMENT)
    assert not directory.exists()
    secret_files.cleanup(tmp_path, DEPLOYMENT)  # idempotent


def test_deployment_id_validated(tmp_path):
    with pytest.raises(SecretError):
        secret_files.deployment_dir(tmp_path, "../../etc")


def test_cli_secret_set_reads_stdin(tmp_path, monkeypatch, capsys):
    import io

    from agentbreeder import cli

    monkeypatch.setattr("sys.stdin", io.TextIOWrapper(io.BytesIO(b"s3cret\n")))
    assert cli.main(["secret", "set", "db", "--state-dir", str(tmp_path)]) == 0
    assert (tmp_path / "secrets" / "db").read_bytes() == b"s3cret"
    assert "s3cret" not in capsys.readouterr().out
    monkeypatch.setattr("sys.stdin", io.TextIOWrapper(io.BytesIO(b"x")))
    assert cli.main(["secret", "set", "Bad/Name", "--state-dir", str(tmp_path)]) == 2


def test_state_dir_env_default(monkeypatch, tmp_path):
    from agentbreeder import cli

    monkeypatch.setenv(cli.STATE_ENV, str(tmp_path / "s"))
    assert cli._default_state() == tmp_path / "s"
    monkeypatch.delenv(cli.STATE_ENV)
    assert cli._default_state().name == "local"


def test_set_secret_creates_store_compatible_state_dir(tmp_path):
    from agentbreeder.store import Store

    state = tmp_path / "fresh" / "state"
    secret_files.set_secret(state, "k", b"v")
    assert state.stat().st_mode & 0o777 == 0o700
    Store(state).close()  # would raise if the state dir were not 0700


def test_preflight_is_metadata_only_and_fifo_safe(tmp_path, monkeypatch):
    secret_files.set_secret(tmp_path, "k", b"v")
    monkeypatch.setattr(secret_files, "_read", lambda path: pytest.fail("value read"))
    secret_files.preflight(tmp_path, {"A": "secret://k"})
    fifo = tmp_path / "secrets" / "pipe"
    os.mkfifo(fifo, 0o600)
    with pytest.raises(SecretError) as error:
        secret_files.preflight(tmp_path, {"A": "secret://pipe"})
    assert error.value.code == "SECRET_MODE"
    monkeypatch.undo()
    with pytest.raises(SecretError):  # O_NONBLOCK: opening a FIFO never hangs
        secret_files._read(fifo)


def test_two_envs_one_secret_share_one_mount(tmp_path):
    secret_files.set_secret(tmp_path, "db", b"v")
    mounts = secret_files.materialise(
        tmp_path, DEPLOYMENT, {"DATABASE_URL": "secret://db", "PG_URL": "secret://db"}
    )
    assert len(mounts) == 1 and mounts[0].envs == ("DATABASE_URL", "PG_URL")


def test_read_rejects_unsafe_files(tmp_path):
    store = tmp_path / "secrets"
    secret_files.set_secret(tmp_path, "k", b"v")
    assert secret_files._read(store / "k") == b"v"
    with pytest.raises(SecretError) as error:
        secret_files._read(store / "missing")
    assert error.value.code == "SECRET_MISSING"
    os.symlink(store / "k", store / "link")
    with pytest.raises(SecretError) as error:
        secret_files._read(store / "link")
    assert error.value.code == "SECRET_MODE"
    (store / "k").chmod(0o640)
    with pytest.raises(SecretError) as error:
        secret_files._read(store / "k")
    assert error.value.code == "SECRET_MODE"
    (store / "empty").write_bytes(b"")
    (store / "empty").chmod(0o600)
    with pytest.raises(SecretError) as error:
        secret_files._read(store / "empty")
    assert error.value.code == "SECRET_EMPTY"
    with pytest.raises(SecretError) as error:
        secret_files.preflight(tmp_path, {"A": "secret://empty"})
    assert error.value.code == "SECRET_EMPTY"


def test_private_dir_tightens_and_refuses_symlinks(tmp_path):
    loose = tmp_path / "loose"
    loose.mkdir(mode=0o755)
    loose.chmod(0o755)
    secret_files._private_dir(loose)
    assert loose.stat().st_mode & 0o777 == 0o700
    real = tmp_path / "real"
    real.mkdir()
    os.symlink(real, tmp_path / "linked")
    with pytest.raises(SecretError):
        secret_files._private_dir(tmp_path / "linked")


def test_set_secret_cleans_temp_on_write_failure(tmp_path, monkeypatch):
    def fail(*args):
        raise OSError("no space")

    secret_files.set_secret(tmp_path, "k", b"old")
    monkeypatch.setattr(secret_files.os, "replace", fail)
    with pytest.raises(OSError):
        secret_files.set_secret(tmp_path, "k", b"new")
    assert (tmp_path / "secrets" / "k").read_bytes() == b"old"
    assert not list((tmp_path / "secrets").glob(".*.tmp"))


def test_cleanup_unlinks_symlinked_dir(tmp_path):
    directory = secret_files.deployment_dir(tmp_path, DEPLOYMENT)
    directory.parent.mkdir(parents=True)
    target = tmp_path / "target"
    target.mkdir()
    os.symlink(target, directory)
    secret_files.cleanup(tmp_path, DEPLOYMENT)
    assert not directory.exists() and target.exists()  # never follows into the target


@pytest.mark.parametrize("name", sorted(secret_files.RESERVED))
def test_reserved_names_never_mount_into_an_agent(tmp_path, name):
    # `services up` writes these host-form database URLs for CLI children (M12 delta §3.5).
    secret_files.set_secret(tmp_path, name, b"postgresql://owner:pw@127.0.0.1:5432/db")
    for check in (
        lambda: secret_files.preflight(tmp_path, {"DATABASE_URL": f"secret://{name}"}),
        lambda: secret_files.materialise(tmp_path, DEPLOYMENT, {"X": f"secret://{name}"}),
    ):
        with pytest.raises(SecretError) as error:
            check()
        assert error.value.code == "SECRET_RESERVED"
        assert "pw" not in str(error.value)
    assert not secret_files.deployment_dir(tmp_path, DEPLOYMENT).exists() or not any(
        secret_files.deployment_dir(tmp_path, DEPLOYMENT).iterdir()
    )


def test_delete_secret_is_idempotent_and_checks_names(tmp_path):
    path = secret_files.set_secret(tmp_path, "agent-database", b"v")
    secret_files.delete_secret(tmp_path, "agent-database")
    assert not path.exists()
    secret_files.delete_secret(tmp_path, "agent-database")
    with pytest.raises(SecretError):
        secret_files.delete_secret(tmp_path, "../x")
    # A symlink is removed, never followed; a directory in its place is a SecretError.
    target = tmp_path / "elsewhere"
    target.write_bytes(b"keep")
    os.symlink(target, tmp_path / "secrets" / "agent-database")
    secret_files.delete_secret(tmp_path, "agent-database")
    assert target.read_bytes() == b"keep"
    (tmp_path / "secrets" / "console-database").mkdir()
    with pytest.raises(SecretError) as error:
        secret_files.delete_secret(tmp_path, "console-database")
    assert error.value.code == "SECRET_MODE"


def test_a_reserved_name_beside_a_normal_one_copies_nothing(tmp_path):
    secret_files.set_secret(tmp_path, "mine", b"ok")
    secret_files.set_secret(tmp_path, "agent-database", b"postgresql://x")
    references = {"API_KEY": "secret://mine", "DATABASE_URL": "secret://agent-database"}
    with pytest.raises(SecretError, match="agent-database"):
        secret_files.materialise(tmp_path, DEPLOYMENT, references)
    assert not (secret_files.deployment_dir(tmp_path, DEPLOYMENT) / "mine").exists()
