from engine.runtimes import base


def test_returns_pinned_agentbreeder_when_installed(monkeypatch):
    monkeypatch.delenv("AGENTBREEDER_RUNTIME_REQUIREMENT", raising=False)
    monkeypatch.setattr(base, "version", lambda dist: "9.9.9")
    assert base.runtime_support_requirement() == "agentbreeder==9.9.9"


def test_falls_back_to_unpinned_when_dist_absent(monkeypatch):
    monkeypatch.delenv("AGENTBREEDER_RUNTIME_REQUIREMENT", raising=False)

    def _raise(_dist):
        raise base.PackageNotFoundError

    monkeypatch.setattr(base, "version", _raise)
    assert base.runtime_support_requirement() == "agentbreeder"


def test_env_override_wins(monkeypatch):
    monkeypatch.setenv("AGENTBREEDER_RUNTIME_REQUIREMENT", "agentbreeder @ file:///wheels/ab.whl")
    assert base.runtime_support_requirement() == "agentbreeder @ file:///wheels/ab.whl"


def test_empty_override_opts_out(monkeypatch):
    monkeypatch.setenv("AGENTBREEDER_RUNTIME_REQUIREMENT", "   ")
    assert base.runtime_support_requirement() is None


def test_pinned_version_override_is_returned_verbatim(monkeypatch):
    monkeypatch.setenv("AGENTBREEDER_RUNTIME_REQUIREMENT", "agentbreeder==2.5.0")
    assert base.runtime_support_requirement() == "agentbreeder==2.5.0"


# ---------------------------------------------------------------------------
# stage_local_wheel — make a local-wheel requirement installable in the image
# ---------------------------------------------------------------------------


def test_stage_local_wheel_copies_and_rewrites(tmp_path):
    """A local wheel path is copied into the build dir and rewritten to its
    bare filename, and the filename is returned for the Dockerfile COPY."""
    wheel = tmp_path / "agentbreeder-2.6.1.dev42+gabc-py3-none-any.whl"
    wheel.write_bytes(b"fake wheel")
    build_dir = tmp_path / "ctx"
    build_dir.mkdir()
    reqs = [str(wheel), "fastapi>=0.110.0"]

    name = base.stage_local_wheel(build_dir, reqs)

    assert name == wheel.name
    assert (build_dir / wheel.name).exists()
    assert reqs[0] == wheel.name  # rewritten in place to bare filename
    assert reqs[1] == "fastapi>=0.110.0"  # other reqs untouched


def test_stage_local_wheel_ignores_pypi_and_url_specs(tmp_path):
    """PyPI specs, VCS URLs, and remote wheel URLs are left untouched."""
    build_dir = tmp_path / "ctx"
    build_dir.mkdir()
    reqs = [
        "agentbreeder==2.6.0",
        "git+https://github.com/org/repo@main",
        "https://example.com/pkg-1.0-py3-none-any.whl",
    ]
    before = list(reqs)

    assert base.stage_local_wheel(build_dir, reqs) is None
    assert reqs == before
    assert not list(build_dir.iterdir())


def test_stage_local_wheel_skips_missing_file(tmp_path):
    """A wheel path that doesn't exist is not treated as a local wheel."""
    build_dir = tmp_path / "ctx"
    build_dir.mkdir()
    reqs = ["dist/does-not-exist-py3-none-any.whl"]

    assert base.stage_local_wheel(build_dir, reqs) is None
    assert reqs == ["dist/does-not-exist-py3-none-any.whl"]
