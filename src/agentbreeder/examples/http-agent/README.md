# HTTP deployment sample

Real health and echo endpoints with no model dependency. This is a development sample, not an authenticated production service.

Recommended: after installing this checkout, run `python src/agentbreeder/examples/http-agent/quickstart.py` to build → deploy → invoke → retry → remove through the installed CLI. It prints and retains private history; no model key is required.

For endpoint development only, run directly with `python3 src/agentbreeder/examples/http-agent/server.py`; it listens on 127.0.0.1:8080 by default. GET /health returns health; POST /invoke accepts JSON bodies up to 64 KiB and returns an explicitly labeled echo.

For a local container:

```sh
docker build -t agentbreeder-http-sample src/agentbreeder/examples/http-agent
docker run --rm --read-only --cap-drop ALL --security-opt no-new-privileges -p 127.0.0.1:8080:8080 agentbreeder-http-sample
```

The sample image runs as UID 65532. Its base image tag is for development; production build/signature/digest policy is future work. The supplied manifest uses an illustrative digest and only demonstrates validation.
