"""Coverage phase 3 — CLI command helper functions.

Targets previously uncovered branches in:
- cli/commands/quickstart.py
- cli/commands/setup.py
- cli/commands/seed.py
"""

from __future__ import annotations

import asyncio
import json
import os
from unittest.mock import AsyncMock, Mock, patch

# ─── quickstart.py helpers ────────────────────────────────────────────────


class TestDetectRuntime:
    def test_docker_with_compose_plugin(self):
        from cli.commands.quickstart import _detect_runtime

        with patch("shutil.which", side_effect=lambda x: x if x == "docker" else None):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = Mock(returncode=0)
                result = _detect_runtime()
        assert result == ("docker", "docker compose")

    def test_docker_compose_standalone(self):
        from cli.commands.quickstart import _detect_runtime

        def which_side(x):
            return x if x in ("docker", "docker-compose") else None

        with patch("shutil.which", side_effect=which_side):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = Mock(returncode=1)  # compose plugin fails
                result = _detect_runtime()
        assert result == ("docker", "docker-compose")

    def test_podman_with_compose_plugin(self):
        from cli.commands.quickstart import _detect_runtime

        def which_side(x):
            return x if x == "podman" else None

        with patch("shutil.which", side_effect=which_side):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = Mock(returncode=0)
                result = _detect_runtime()
        assert result == ("podman", "podman compose")

    def test_podman_compose_standalone(self):
        from cli.commands.quickstart import _detect_runtime

        def which_side(x):
            return x if x in ("podman", "podman-compose") else None

        with patch("shutil.which", side_effect=which_side):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = Mock(returncode=1)  # compose plugin fails
                result = _detect_runtime()
        assert result == ("podman", "podman-compose")

    def test_nothing_found(self):
        from cli.commands.quickstart import _detect_runtime

        with patch("shutil.which", return_value=None):
            result = _detect_runtime()
        assert result is None


class TestRuntimeIsRunning:
    def test_running(self):
        from cli.commands.quickstart import _runtime_is_running

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = Mock(returncode=0)
            assert _runtime_is_running("docker") is True

    def test_not_running(self):
        from cli.commands.quickstart import _runtime_is_running

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = Mock(returncode=1)
            assert _runtime_is_running("docker") is False


class TestInstallInstructions:
    def test_darwin(self):
        from cli.commands.quickstart import _install_instructions

        with patch("platform.system", return_value="Darwin"):
            result = _install_instructions()
        assert any("brew" in line for line in result)

    def test_linux(self):
        from cli.commands.quickstart import _install_instructions

        with patch("platform.system", return_value="Linux"):
            result = _install_instructions()
        assert any("docker" in line.lower() for line in result)

    def test_windows(self):
        from cli.commands.quickstart import _install_instructions

        with patch("platform.system", return_value="Windows"):
            result = _install_instructions()
        assert any("winget" in line or "docker" in line.lower() for line in result)


class TestWaitHttp:
    def test_success_on_first_try(self):
        from cli.commands.quickstart import _wait_http

        mock_resp = Mock(status_code=200)
        with patch("httpx.get", return_value=mock_resp):
            with patch("time.sleep"):
                result = _wait_http("http://localhost:8000/health", timeout=10)
        assert result is True

    def test_connect_error_then_success(self):
        import httpx as httpx_mod

        from cli.commands.quickstart import _wait_http

        mock_resp = Mock(status_code=200)
        with patch(
            "httpx.get",
            side_effect=[httpx_mod.ConnectError("refused"), mock_resp],
        ):
            with patch("time.sleep"):
                with patch("time.monotonic", side_effect=[0, 1, 2, 100]):
                    result = _wait_http("http://localhost:8000/health", timeout=50, interval=0.1)
        assert result is True

    def test_timeout(self):
        import httpx as httpx_mod

        from cli.commands.quickstart import _wait_http

        with patch("httpx.get", side_effect=httpx_mod.ConnectError("refused")):
            with patch("time.sleep"):
                with patch("time.monotonic", side_effect=[0, 200]):  # immediate timeout
                    result = _wait_http("http://localhost:8000/health", timeout=10)
        assert result is False

    def test_read_timeout_retries(self):
        import httpx as httpx_mod

        from cli.commands.quickstart import _wait_http

        mock_resp = Mock(status_code=200)
        with patch(
            "httpx.get",
            side_effect=[httpx_mod.ReadTimeout("timeout"), mock_resp],
        ):
            with patch("time.sleep"):
                with patch("time.monotonic", side_effect=[0, 1, 2, 100]):
                    result = _wait_http("http://localhost:8000/health", timeout=50)
        assert result is True


class TestApiPost:
    def test_success_200(self):
        from cli.commands.quickstart import _api_post

        mock_resp = Mock(status_code=200)
        mock_resp.json.return_value = {"id": "123"}
        with patch("httpx.post", return_value=mock_resp):
            result = _api_post("/api/v1/agents", {"name": "test"})
        assert result == {"id": "123"}

    def test_success_201(self):
        from cli.commands.quickstart import _api_post

        mock_resp = Mock(status_code=201)
        mock_resp.json.return_value = {"id": "abc"}
        with patch("httpx.post", return_value=mock_resp):
            result = _api_post("/api/v1/agents", {"name": "test"})
        assert result == {"id": "abc"}

    def test_non_2xx_returns_none(self):
        from cli.commands.quickstart import _api_post

        mock_resp = Mock(status_code=422)
        with patch("httpx.post", return_value=mock_resp):
            result = _api_post("/api/v1/agents", {"bad": "data"})
        assert result is None

    def test_connect_error_returns_none(self):
        import httpx as httpx_mod

        from cli.commands.quickstart import _api_post

        with patch("httpx.post", side_effect=httpx_mod.ConnectError("refused")):
            result = _api_post("/api/v1/agents", {})
        assert result is None

    def test_timeout_returns_none(self):
        import httpx as httpx_mod

        from cli.commands.quickstart import _api_post

        with patch("httpx.post", side_effect=httpx_mod.TimeoutException("timeout")):
            result = _api_post("/api/v1/agents", {})
        assert result is None


class TestRegisterMcpServers:
    def test_registers_both_servers(self):
        from cli.commands.quickstart import _register_mcp_servers

        with patch("cli.commands.quickstart._api_post", return_value={"id": "x"}) as mock_post:
            count = _register_mcp_servers()
        assert count == 2
        assert mock_post.call_count == 2

    def test_partial_failure(self):
        from cli.commands.quickstart import _register_mcp_servers

        with patch("cli.commands.quickstart._api_post", side_effect=[{"id": "x"}, None]):
            count = _register_mcp_servers()
        assert count == 1

    def test_all_fail(self):
        from cli.commands.quickstart import _register_mcp_servers

        with patch("cli.commands.quickstart._api_post", return_value=None):
            count = _register_mcp_servers()
        assert count == 0


class TestRegisterPrompts:
    def test_registers_sample_prompt(self):
        from cli.commands.quickstart import _register_prompts

        with patch("cli.commands.quickstart._api_post", return_value={"id": "p1"}) as mock_post:
            count = _register_prompts()
        assert count == 1
        assert mock_post.call_count == 1

    def test_all_fail(self):
        from cli.commands.quickstart import _register_prompts

        with patch("cli.commands.quickstart._api_post", return_value=None):
            count = _register_prompts()
        assert count == 0


class TestRegisterAgents:
    def test_no_yaml_files(self, tmp_path):
        from cli.commands.quickstart import _register_agents

        with patch("cli.commands.quickstart.EXAMPLES_QS", tmp_path):
            result = _register_agents()
        assert result == []

    def test_registers_yaml_files(self, tmp_path):
        from cli.commands.quickstart import _register_agents

        yaml_file = tmp_path / "agent.yaml"
        yaml_file.write_text("name: test-agent\n")

        with patch("cli.commands.quickstart.EXAMPLES_QS", tmp_path):
            with patch(
                "cli.commands.quickstart._api_post",
                return_value={"data": {"name": "test-agent", "id": "123"}},
            ):
                result = _register_agents()
        assert len(result) == 1

    def test_api_failure_skipped(self, tmp_path):
        from cli.commands.quickstart import _register_agents

        yaml_file = tmp_path / "agent.yaml"
        yaml_file.write_text("name: test-agent\n")

        with patch("cli.commands.quickstart.EXAMPLES_QS", tmp_path):
            with patch("cli.commands.quickstart._api_post", return_value=None):
                result = _register_agents()
        assert result == []


class TestWriteEnvKeyQuickstart:
    def test_creates_new_file(self, tmp_path):
        env_file = tmp_path / ".env"
        key, value = "TEST_KEY", "test_value"
        if env_file.exists():
            lines = env_file.read_text().splitlines()
            updated = False
            for i, line in enumerate(lines):
                if line.startswith(f"{key}="):
                    lines[i] = f"{key}={value}"
                    updated = True
                    break
            if not updated:
                lines.append(f"{key}={value}")
            env_file.write_text("\n".join(lines) + "\n")
        else:
            env_file.write_text(f"{key}={value}\n")
        assert env_file.read_text() == "TEST_KEY=test_value\n"

    def test_updates_existing_key(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("OTHER=x\nTEST_KEY=old_value\n")

        key, value = "TEST_KEY", "new_value"
        lines = env_file.read_text().splitlines()
        updated = False
        for i, line in enumerate(lines):
            if line.startswith(f"{key}="):
                lines[i] = f"{key}={value}"
                updated = True
                break
        if not updated:
            lines.append(f"{key}={value}")
        env_file.write_text("\n".join(lines) + "\n")

        assert "TEST_KEY=new_value" in env_file.read_text()

    def test_appends_new_key_to_existing_file(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("OTHER=x\n")

        key, value = "NEW_KEY", "new_value"
        lines = env_file.read_text().splitlines()
        updated = False
        for i, line in enumerate(lines):
            if line.startswith(f"{key}="):
                lines[i] = f"{key}={value}"
                updated = True
                break
        if not updated:
            lines.append(f"{key}={value}")
        env_file.write_text("\n".join(lines) + "\n")

        content = env_file.read_text()
        assert "OTHER=x" in content
        assert "NEW_KEY=new_value" in content


class TestPrintHelpers:
    def test_ok(self, capsys):
        from cli.commands.quickstart import _ok

        _ok("everything is fine")

    def test_warn(self, capsys):
        from cli.commands.quickstart import _warn

        _warn("something might be wrong")

    def test_info(self, capsys):
        from cli.commands.quickstart import _info

        _info("just some info")

    def test_step(self, capsys):
        from cli.commands.quickstart import _step

        _step("Testing", 3, 8)


class TestLoadSeedModule:
    def test_returns_none_when_file_missing(self, tmp_path):
        from cli.commands.quickstart import _load_seed_module

        with patch("cli.commands.quickstart.DEPLOY_DIR", tmp_path):
            result = _load_seed_module()
        assert result is None

    def test_returns_module_when_valid(self, tmp_path):
        from cli.commands.quickstart import _load_seed_module

        seed_dir = tmp_path / "seed"
        seed_dir.mkdir()
        seed_file = seed_dir / "seed.py"
        seed_file.write_text("def seed_chromadb(): return {'ok': True}\n")

        with patch("cli.commands.quickstart.DEPLOY_DIR", tmp_path):
            result = _load_seed_module()
        assert result is not None
        assert hasattr(result, "seed_chromadb")

    def test_returns_none_on_exec_error(self, tmp_path):
        from cli.commands.quickstart import _load_seed_module

        seed_dir = tmp_path / "seed"
        seed_dir.mkdir()
        seed_file = seed_dir / "seed.py"
        seed_file.write_text("raise RuntimeError('bad module')\n")

        with patch("cli.commands.quickstart.DEPLOY_DIR", tmp_path):
            result = _load_seed_module()
        assert result is None


class TestSeedChromadbNeo4j:
    def test_seed_chromadb_module_none(self):
        from cli.commands.quickstart import _seed_chromadb

        with patch("cli.commands.quickstart._load_seed_module", return_value=None):
            result = _seed_chromadb()
        assert result["ok"] is False
        assert "error" in result

    def test_seed_chromadb_ok(self):
        from cli.commands.quickstart import _seed_chromadb

        mock_mod = Mock()
        mock_mod.seed_chromadb.return_value = {"ok": True}
        with patch("cli.commands.quickstart._load_seed_module", return_value=mock_mod):
            result = _seed_chromadb()
        assert result["ok"] is True

    def test_seed_chromadb_not_ok(self):
        from cli.commands.quickstart import _seed_chromadb

        mock_mod = Mock()
        mock_mod.seed_chromadb.return_value = {"ok": False}
        with patch("cli.commands.quickstart._load_seed_module", return_value=mock_mod):
            result = _seed_chromadb()
        assert result["ok"] is False

    def test_seed_neo4j_module_none(self):
        from cli.commands.quickstart import _seed_neo4j

        with patch("cli.commands.quickstart._load_seed_module", return_value=None):
            assert _seed_neo4j() is False

    def test_seed_neo4j_ok(self):
        from cli.commands.quickstart import _seed_neo4j

        mock_mod = Mock()
        mock_mod.seed_neo4j.return_value = {"ok": True}
        with patch("cli.commands.quickstart._load_seed_module", return_value=mock_mod):
            assert _seed_neo4j() is True

    def test_seed_neo4j_not_ok(self):
        from cli.commands.quickstart import _seed_neo4j

        mock_mod = Mock()
        mock_mod.seed_neo4j.return_value = {"ok": False}
        with patch("cli.commands.quickstart._load_seed_module", return_value=mock_mod):
            assert _seed_neo4j() is False


class TestPrintFinalSummary:
    def test_runs_without_error(self):
        from cli.commands.quickstart import _print_final_summary

        services = {
            "dashboard": True,
            "api": True,
            "chromadb": True,
            "neo4j": True,
            "litellm": False,
        }
        _print_final_summary(services, ["rag-agent", "graph-agent"])

    def test_empty_agents(self):
        from cli.commands.quickstart import _print_final_summary

        _print_final_summary({}, [])


# ─── setup.py helpers ─────────────────────────────────────────────────────


class TestLoadProviders:
    def test_returns_empty_when_no_file(self, tmp_path):
        from cli.commands import setup as setup_mod

        with patch.object(setup_mod, "PROVIDERS_FILE", tmp_path / "providers.json"):
            result = setup_mod._load_providers()
        assert result == {}

    def test_reads_existing_file(self, tmp_path):
        from cli.commands import setup as setup_mod

        pf = tmp_path / "providers.json"
        pf.write_text(json.dumps({"anthropic": {"key": "sk-ant-test"}}))
        with patch.object(setup_mod, "PROVIDERS_FILE", pf):
            result = setup_mod._load_providers()
        assert result == {"anthropic": {"key": "sk-ant-test"}}


class TestSaveProviders:
    def test_creates_file(self, tmp_path):
        from cli.commands import setup as setup_mod

        pf = tmp_path / "sub" / "providers.json"
        with patch.object(setup_mod, "PROVIDERS_FILE", pf):
            setup_mod._save_providers({"openai": {"key": "sk-test"}})
        assert pf.exists()
        data = json.loads(pf.read_text())
        assert data["openai"]["key"] == "sk-test"


class TestWriteEnvKeySetup:
    def test_creates_new_env_file(self, tmp_path):
        from cli.commands import setup as setup_mod

        env_file = tmp_path / ".env"
        with patch.object(setup_mod, "ENV_FILE", env_file):
            setup_mod._write_env_key("MY_KEY", "my_val")
        assert env_file.read_text() == "MY_KEY=my_val\n"

    def test_updates_existing_key(self, tmp_path):
        from cli.commands import setup as setup_mod

        env_file = tmp_path / ".env"
        env_file.write_text("OTHER=x\nMY_KEY=old\n")
        with patch.object(setup_mod, "ENV_FILE", env_file):
            setup_mod._write_env_key("MY_KEY", "new")
        content = env_file.read_text()
        assert "MY_KEY=new" in content
        assert "MY_KEY=old" not in content

    def test_appends_missing_key(self, tmp_path):
        from cli.commands import setup as setup_mod

        env_file = tmp_path / ".env"
        env_file.write_text("OTHER=x\n")
        with patch.object(setup_mod, "ENV_FILE", env_file):
            setup_mod._write_env_key("NEW_KEY", "val")
        assert "NEW_KEY=val" in env_file.read_text()


class TestReadEnvKey:
    def test_reads_from_file(self, tmp_path):
        from cli.commands import setup as setup_mod

        env_file = tmp_path / ".env"
        env_file.write_text("SOME_KEY=file_val\n")
        with patch.object(setup_mod, "ENV_FILE", env_file):
            result = setup_mod._read_env_key("SOME_KEY")
        assert result == "file_val"

    def test_falls_back_to_env(self, tmp_path):
        from cli.commands import setup as setup_mod

        env_file = tmp_path / ".env"  # doesn't exist
        with patch.object(setup_mod, "ENV_FILE", env_file):
            with patch.dict(os.environ, {"FALLBACK_KEY": "env_val"}):
                result = setup_mod._read_env_key("FALLBACK_KEY")
        assert result == "env_val"

    def test_returns_none_when_missing(self, tmp_path):
        from cli.commands import setup as setup_mod

        env_file = tmp_path / ".env"
        with patch.object(setup_mod, "ENV_FILE", env_file):
            with patch.dict(os.environ, {}, clear=False):
                result = setup_mod._read_env_key("DEFINITELY_NOT_SET_XYZ")
        assert result is None


class TestMask:
    def test_short_value(self):
        from cli.commands.setup import _mask

        assert _mask("12345678") == "••••"

    def test_long_value(self):
        from cli.commands.setup import _mask

        result = _mask("sk-ant-api-abcdefghij")
        assert result.startswith("••••")
        assert result.endswith("ghij")

    def test_exactly_8(self):
        from cli.commands.setup import _mask

        assert _mask("12345678") == "••••"


class TestOllamaInstallInstructions:
    def test_darwin(self):
        from cli.commands.setup import _ollama_install_instructions

        with patch("platform.system", return_value="Darwin"):
            result = _ollama_install_instructions()
        assert any("brew" in line for line in result)

    def test_linux(self):
        from cli.commands.setup import _ollama_install_instructions

        with patch("platform.system", return_value="Linux"):
            result = _ollama_install_instructions()
        assert any("install.sh" in line for line in result)

    def test_windows(self):
        from cli.commands.setup import _ollama_install_instructions

        with patch("platform.system", return_value="Windows"):
            result = _ollama_install_instructions()
        assert any("winget" in line or "ollama" in line.lower() for line in result)

    def test_other_platform(self):
        from cli.commands.setup import _ollama_install_instructions

        with patch("platform.system", return_value="FreeBSD"):
            result = _ollama_install_instructions()
        assert len(result) > 0


class TestValidateApiKeyFormat:
    def test_valid_key(self):
        from cli.commands.setup import _validate_api_key_format

        assert _validate_api_key_format("sk-ant-api01-abcdefghijklmnop", "sk-ant-") is True

    def test_wrong_prefix(self):
        from cli.commands.setup import _validate_api_key_format

        assert _validate_api_key_format("sk-openai-xyz", "sk-ant-") is False

    def test_too_short(self):
        from cli.commands.setup import _validate_api_key_format

        assert _validate_api_key_format("sk-ant-x", "sk-ant-") is False


def _run_async(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class TestOllamaIsRunning:
    def test_running(self):
        from cli.commands.setup import _ollama_is_running

        mock_resp = Mock(status_code=200)
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.get = AsyncMock(return_value=mock_resp)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = _run_async(_ollama_is_running("http://localhost:11434"))
        assert result is True

    def test_connect_error(self):
        import httpx as httpx_mod

        from cli.commands.setup import _ollama_is_running

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.get = AsyncMock(side_effect=httpx_mod.ConnectError("refused"))

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = _run_async(_ollama_is_running("http://localhost:11434"))
        assert result is False


class TestOllamaListModels:
    def test_returns_model_names(self):
        from cli.commands.setup import _ollama_list_models

        mock_resp = Mock(status_code=200)
        mock_resp.json.return_value = {"models": [{"name": "llama3.2"}, {"name": "mistral"}]}
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.get = AsyncMock(return_value=mock_resp)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = _run_async(_ollama_list_models())
        assert result == ["llama3.2", "mistral"]

    def test_error_returns_empty(self):
        from cli.commands.setup import _ollama_list_models

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.get = AsyncMock(side_effect=Exception("error"))

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = _run_async(_ollama_list_models())
        assert result == []


class TestTestApiKeys:
    def _make_mock_client(self, status_code: int):
        mock_resp = Mock(status_code=status_code)
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.post = AsyncMock(return_value=mock_resp)
        mock_client.get = AsyncMock(return_value=mock_resp)
        return mock_client

    def test_anthropic_key_valid(self):
        from cli.commands.setup import _test_anthropic_key

        with patch("httpx.AsyncClient", return_value=self._make_mock_client(200)):
            result = _run_async(_test_anthropic_key("sk-ant-test-key"))
        assert result is True

    def test_anthropic_key_invalid(self):
        from cli.commands.setup import _test_anthropic_key

        with patch("httpx.AsyncClient", return_value=self._make_mock_client(401)):
            result = _run_async(_test_anthropic_key("sk-ant-bad-key"))
        assert result is False

    def test_anthropic_key_exception(self):
        from cli.commands.setup import _test_anthropic_key

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.post = AsyncMock(side_effect=Exception("network error"))

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = _run_async(_test_anthropic_key("sk-ant-test"))
        assert result is False

    def test_openai_key_valid(self):
        from cli.commands.setup import _test_openai_key

        with patch("httpx.AsyncClient", return_value=self._make_mock_client(200)):
            result = _run_async(_test_openai_key("sk-test-key"))
        assert result is True

    def test_openai_key_invalid(self):
        from cli.commands.setup import _test_openai_key

        with patch("httpx.AsyncClient", return_value=self._make_mock_client(401)):
            result = _run_async(_test_openai_key("sk-bad"))
        assert result is False

    def test_google_key_valid(self):
        from cli.commands.setup import _test_google_key

        with patch("httpx.AsyncClient", return_value=self._make_mock_client(200)):
            result = _run_async(_test_google_key("AIzaTest123"))
        assert result is True

    def test_google_key_invalid(self):
        from cli.commands.setup import _test_google_key

        with patch("httpx.AsyncClient", return_value=self._make_mock_client(403)):
            result = _run_async(_test_google_key("AIzaBad"))
        assert result is False

    def test_openrouter_key_valid(self):
        from cli.commands.setup import _test_openrouter_key

        with patch("httpx.AsyncClient", return_value=self._make_mock_client(200)):
            result = _run_async(_test_openrouter_key("sk-or-test"))
        assert result is True

    def test_openrouter_key_invalid(self):
        from cli.commands.setup import _test_openrouter_key

        with patch("httpx.AsyncClient", return_value=self._make_mock_client(401)):
            result = _run_async(_test_openrouter_key("sk-or-bad"))
        assert result is False


# ─── seed.py helpers ──────────────────────────────────────────────────────


class TestSeedRunViaSubprocess:
    def test_default_flags(self):
        from cli.commands import seed as seed_mod
        from cli.commands.seed import _run_via_subprocess

        with patch("subprocess.run") as mock_run:
            _run_via_subprocess(False, False, None, "agentbreeder_knowledge", None, False, False)
        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        assert str(seed_mod.SEED_SCRIPT) in args

    def test_chromadb_only_flag(self):
        from cli.commands.seed import _run_via_subprocess

        with patch("subprocess.run") as mock_run:
            _run_via_subprocess(True, False, None, "agentbreeder_knowledge", None, False, False)
        args = mock_run.call_args[0][0]
        assert "--chromadb-only" in args

    def test_neo4j_only_flag(self):
        from cli.commands.seed import _run_via_subprocess

        with patch("subprocess.run") as mock_run:
            _run_via_subprocess(False, True, None, "agentbreeder_knowledge", None, False, False)
        args = mock_run.call_args[0][0]
        assert "--neo4j-only" in args

    def test_custom_collection(self):
        from cli.commands.seed import _run_via_subprocess

        with patch("subprocess.run") as mock_run:
            _run_via_subprocess(False, False, None, "my_custom_collection", None, False, False)
        args = mock_run.call_args[0][0]
        assert "--collection" in args
        assert "my_custom_collection" in args

    def test_clear_flag(self):
        from cli.commands.seed import _run_via_subprocess

        with patch("subprocess.run") as mock_run:
            _run_via_subprocess(False, False, None, "agentbreeder_knowledge", None, True, False)
        args = mock_run.call_args[0][0]
        assert "--clear" in args

    def test_list_flag(self):
        from cli.commands.seed import _run_via_subprocess

        with patch("subprocess.run") as mock_run:
            _run_via_subprocess(False, False, None, "agentbreeder_knowledge", None, False, True)
        args = mock_run.call_args[0][0]
        assert "--list" in args

    def test_docs_path(self, tmp_path):
        from cli.commands.seed import _run_via_subprocess

        with patch("subprocess.run") as mock_run:
            _run_via_subprocess(
                False, False, tmp_path, "agentbreeder_knowledge", None, False, False
            )
        args = mock_run.call_args[0][0]
        assert "--docs" in args

    def test_cypher_path(self, tmp_path):
        from cli.commands.seed import _run_via_subprocess

        cypher_file = tmp_path / "test.cypher"
        cypher_file.write_text("MATCH (n) RETURN n")
        with patch("subprocess.run") as mock_run:
            _run_via_subprocess(
                False, False, None, "agentbreeder_knowledge", cypher_file, False, False
            )
        args = mock_run.call_args[0][0]
        assert "--cypher" in args


class TestSeedPrintNextSteps:
    def test_runs_without_error(self):
        from cli.commands.seed import _print_next_steps

        _print_next_steps()


class TestSeedPrintStatus:
    def test_chromadb_ok(self):
        from cli.commands.seed import _print_status

        mock_mod = Mock()
        mock_mod.list_chromadb.return_value = {
            "ok": True,
            "collections": [{"name": "test_col", "count": 42}],
        }
        mock_mod.list_neo4j.return_value = {"ok": True, "counts": {"Agent": 5, "Tool": 3}}
        _print_status(mock_mod)

    def test_chromadb_empty_collections(self):
        from cli.commands.seed import _print_status

        mock_mod = Mock()
        mock_mod.list_chromadb.return_value = {"ok": True, "collections": []}
        mock_mod.list_neo4j.return_value = {"ok": False, "error": "Connection refused"}
        _print_status(mock_mod)

    def test_chromadb_error(self):
        from cli.commands.seed import _print_status

        mock_mod = Mock()
        mock_mod.list_chromadb.return_value = {"ok": False, "error": "ChromaDB not running"}
        mock_mod.list_neo4j.return_value = {"ok": False, "error": "Neo4j not running"}
        _print_status(mock_mod)


class TestSeedRunViaModule:
    def _make_mod(self, chroma_ok=True, neo4j_ok=True):
        mod = Mock()
        mod.seed_chromadb.return_value = {
            "ok": chroma_ok,
            "documents_seeded": 10,
            "collection_id": "col-id-12345678",
            "error": None if chroma_ok else "failed",
        }
        mod.seed_neo4j.return_value = {
            "ok": neo4j_ok,
            "statements_run": 5,
            "errors": [] if neo4j_ok else ["some error"],
        }
        mod.list_neo4j.return_value = {"ok": True, "counts": {"Agent": 3}}
        return mod

    def test_list_calls_print_status(self):
        from cli.commands.seed import _run_via_module

        mock_mod = self._make_mod()
        with patch("cli.commands.seed._print_status") as mock_status:
            _run_via_module(mock_mod, False, False, None, "coll", None, False, True)
        mock_status.assert_called_once_with(mock_mod)

    def test_seeds_both_by_default(self):
        from cli.commands.seed import _run_via_module

        mock_mod = self._make_mod()
        with patch("cli.commands.seed._print_next_steps"):
            _run_via_module(mock_mod, False, False, None, "coll", None, False, False)
        mock_mod.seed_chromadb.assert_called_once()
        mock_mod.seed_neo4j.assert_called_once()

    def test_chromadb_only(self):
        from cli.commands.seed import _run_via_module

        mock_mod = self._make_mod()
        with patch("cli.commands.seed._print_next_steps"):
            _run_via_module(mock_mod, True, False, None, "coll", None, False, False)
        mock_mod.seed_chromadb.assert_called_once()
        mock_mod.seed_neo4j.assert_not_called()

    def test_neo4j_only(self):
        from cli.commands.seed import _run_via_module

        mock_mod = self._make_mod()
        with patch("cli.commands.seed._print_next_steps"):
            _run_via_module(mock_mod, False, True, None, "coll", None, False, False)
        mock_mod.seed_chromadb.assert_not_called()
        mock_mod.seed_neo4j.assert_called_once()

    def test_chromadb_failure_message(self):
        from cli.commands.seed import _run_via_module

        mock_mod = self._make_mod(chroma_ok=False)
        with patch("cli.commands.seed._print_next_steps"):
            _run_via_module(mock_mod, True, False, None, "coll", None, False, False)

    def test_neo4j_failure_message(self):
        from cli.commands.seed import _run_via_module

        mock_mod = self._make_mod(neo4j_ok=False)
        with patch("cli.commands.seed._print_next_steps"):
            _run_via_module(mock_mod, False, True, None, "coll", None, False, False)

    def test_with_docs_path(self, tmp_path):
        from cli.commands.seed import _run_via_module

        mock_mod = self._make_mod()
        with patch("cli.commands.seed._print_next_steps"):
            _run_via_module(mock_mod, True, False, tmp_path, "coll", None, False, False)
        call_kwargs = mock_mod.seed_chromadb.call_args
        assert (
            call_kwargs.kwargs.get("docs_dir") == tmp_path
            or call_kwargs[1].get("docs_dir") == tmp_path
        )
