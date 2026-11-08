"""Real local Docker test, enabled explicitly. Mock tests are not deployment evidence."""

import json
import os
import subprocess
import uuid
from pathlib import Path
from urllib.request import Request, urlopen

import pytest
from conftest import v2_manifest_data

from agentbreeder.deployment import DeploymentService
from agentbreeder.manifest import Manifest
from agentbreeder.runtime import Docker
from agentbreeder.store import Store


@pytest.mark.skipif(os.getenv("AGENTBREEDER_DOCKER_TEST") != "1", reason="Opt-in real Docker test")
def test_real_docker_lifecycle(tmp_path):
    runtime = Docker()
    runtime.authorize()
    image_file = tmp_path / "image-id"
    # The sample builds to a deterministic image ID that other runs or the quickstart may also
    # reference, so `image rm <id>` can fail. Remove this run's unique tag instead.
    tag = "agentbreeder-test:" + uuid.uuid4().hex[:12]
    image = None
    store = Store(tmp_path / "state")
    service = DeploymentService(store, runtime)
    result = None
    try:
        subprocess.run(
            [
                "docker",
                "--context",
                runtime.context,
                "build",
                "--iidfile",
                str(image_file),
                "--tag",
                tag,
                str(Path(__file__).parents[1] / "examples/http-agent"),
            ],
            check=True,
            timeout=180,
            capture_output=True,
        )
        image = image_file.read_text().strip()
        manifest = Manifest.model_validate(
            v2_manifest_data(name="test-" + uuid.uuid4().hex[:12], image=image)
        )
        result = service.deploy(manifest, local=True)
        assert result["gate"]["checks_unavailable"] == [
            "policy",
            "approval",
            "deployer_verification",
        ]
        assert result["state"] == "ready", (result.get("error"), result.get("events"))
        with urlopen(
            Request(result["endpoint"] + "/invoke", data=b'{"message":"hello"}'), timeout=3
        ) as response:
            assert json.load(response)["input"]["message"] == "hello"
        assert service.deploy(manifest, local=True)["id"] == result["id"]
        store.close()
        store = Store(tmp_path / "state")
        service = DeploymentService(store, runtime)
        assert store.get(result["id"])["state"] == "ready"
        assert service.remove(result["id"], local=True)["state"] == "removed"
    finally:
        if result:
            service.remove(result["id"], local=True)
        store.close()
        if image:
            runtime.command("image", "rm", tag)


@pytest.mark.skipif(os.getenv("AGENTBREEDER_DOCKER_TEST") != "1", reason="Opt-in real Docker test")
def test_real_docker_secret_file(tmp_path):
    """#727: the agent (uid 65532, read-only rootfs) reads a mounted secret file; the value is
    absent from `docker inspect`, and the host copy is deleted on remove."""
    from agentbreeder import secret_files

    runtime = Docker()
    runtime.authorize()
    image_file = tmp_path / "image-id"
    tag = "agentbreeder-test:" + uuid.uuid4().hex[:12]
    store = Store(tmp_path / "state")
    service = DeploymentService(store, runtime)
    value = "secret-" + uuid.uuid4().hex
    secret_files.set_secret(tmp_path / "state", "db", value.encode())
    result = None
    try:
        subprocess.run(
            ["docker", "--context", runtime.context, "build", "--iidfile", str(image_file),
             "--tag", tag, str(Path(__file__).parents[1] / "examples/http-agent")],
            check=True, timeout=180, capture_output=True,
        )  # fmt: skip
        manifest = Manifest.model_validate(
            v2_manifest_data(
                name="test-" + uuid.uuid4().hex[:12],
                image=image_file.read_text().strip(),
                secrets={"DATABASE_URL": "secret://db"},
            )
        )
        result = service.deploy(manifest, local=True)
        assert result["state"] == "ready", result.get("error")
        container = runtime.inspect(store.get(result["id"]))
        script = "import os; print(open(os.environ['DATABASE_URL_FILE']).read())"
        read = runtime.command("exec", container["Id"], "python", "-c", script)
        assert read.strip() == value
        assert value not in json.dumps(container)
        assert container["Config"]["User"] == "65532:65532"
        assert service.remove(result["id"], local=True)["state"] == "removed"
        assert not secret_files.deployment_dir(tmp_path / "state", result["id"]).exists()
        result = None
    finally:
        if result:
            service.remove(result["id"], local=True)
        store.close()
        runtime.command("image", "rm", tag)
