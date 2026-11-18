# #1077 investigation: the default bridge network gets a new ID (Docker Desktop Resource Saver)

Status: **cause of the bridge-ID changes found and reproduced; not ours** (branched from `main`
at 3f13edb4, 2026-11-18, macOS 13.7.8
x86_64, Docker Desktop 4.38.0, engine 27.5.1). Docker Desktop's Resource Saver stops its Linux VM
after 5 minutes without a running container. The next call that is not answered from Docker
Desktop's cache boots the VM again, and the new `dockerd` creates a new default `bridge` network.
No AgentBreeder command restarts Docker. `services up` does not detect the restart; the change it
needs is described below and is **not made** in this PR. Two of the reports are not explained: the recreated `Docker.raw`, and why the VM did not wake in
the "partly wedged" report.

The other three tasks of this session (#1048, #1111, #1019) were **not run**: the reproduction
below showed "create succeeds but the object is missing", which is one of the session's stop
conditions, so the session stopped after recording it.

This is a record only. It is on `main`, not in a published preview.

## What was read

Read-only, on the machine where the reports were made:
- Docker Desktop's host logs (`~/Library/Containers/com.docker.docker/Data/log/host/`:
  `com.docker.backend.log*`, `electron-*.log`, `Docker.log`) and VM logs (`…/log/vm/dockerd.log*`).
  `dockerd.log*` covers 2026-11-16 02:48 UTC onwards (and some hours before) and the backend logs
  2026-11-17 07:54 UTC onwards.
- `~/Library/Group Containers/group.com.docker/settings-store.json`: `AutoPauseTimeoutSeconds: 300`,
  `AutoStart: false`, 4 CPUs, 8092 MiB memory, `DiskSizeMiB: 1048576`.
- Host: 194 GiB free disk, 48 GiB memory. `Docker.raw` uses 15 GiB on the host.
  `~/Library/Logs/DiagnosticReports` has no Docker crash report; its one
  `com.apple.Virtualization.VirtualMachine` report is from 2026-11-13, before the first report here.
- AgentBreeder's Docker calls (`src/agentbreeder/services/compose.py`, `services/engine.py`,
  `runtime.py`): no call restarts, stops or reconfigures the engine.

## Evidence in the logs

| Finding | Evidence |
|---|---|
| The daemon stops and starts many times a day | `dockerd.log*`: `Daemon has completed initialization` 11 times on 2026-11-16, 5 on 11-17 and 4 on 11-18 (UTC, before this session); `Processing signal 'terminated'` 12, 5 and 3 times |
| Every `dockerd` termination in the retained backend logs is the idle timer (the app itself was also quit once, after the last of them; see Not explained) | From 2026-11-17 07:54 UTC to this session: 6 `dockerd` terminations and 6 `[idle] stopping VM` lines, each after `timer expired (5m0s)` and `system idle for 5m0s -> reducing`. Two runs lasted exactly 5 min 40 s (2026-11-17 15:30:35 to 15:36:15 and 2026-11-18 07:30:35 to 07:36:15 UTC) |
| The reported times are daemon starts | `Daemon has completed initialization` at 2026-11-16 05:43:32, 2026-11-16 19:09:41, 2026-11-17 00:23:39 and 2026-11-17 08:30:35 UTC match the reports of 05:43:31, about 19:09, 00:23:39 and 08:30. The report of 2026-11-15 23:48:33 UTC is older than the retained logs |
| The "partly wedged" report (2026-11-16 05:58 to 06:12 UTC) falls in a stopped period | `dockerd` was terminated at 05:56:21 and next started at 07:03:43 UTC. `docker version` and `docker ps -a` answering while `docker run` hangs is what a stopped VM with a cache looks like (below). Why the VM did not wake that time is **not known** |
| A wake can take longer than Docker Desktop allows | `[idle][E] starting VM: starting vm: context deadline exceeded` twice in the retained logs (2026-11-18 05:08:19 and 23:15:36 UTC), each 60 s after `starting VM`. Other wakes took 31 s and 60 s |

## Reproduction (run 2026-11-18, UTC)

Nothing was started or stopped by hand. With no container running, no Docker command was sent
for 5 minutes; then the commands below were run.

| Time | What | Result |
|---|---|---|
| 23:07:45 | last API call ends | `[idle] busy -> idle`, `timer started (5m0s)`. `bridge` is `c62046ba…`, created 23:04:42 |
| 23:12:45 | nothing | `timer expired (5m0s)`, `system idle for 5m0s -> reducing`, `stopping VM`; `dockerd` gets `terminated` at 23:12:47; `VM stopped` at 23:12:48 |
| 23:14:14 | `docker network ls`, `docker ps -a` | answered in 0.13 s from the cache (`apicache cache << GET /networks`); `bridge` still `c62046ba…` |
| 23:14:24 | `docker network inspect bridge`, `docker info` | answered from the cache in 0.3 s and 0.9 s; the VM stays stopped |
| 23:14:36 | `docker network create ablv-probe-1077` | takes **82 s** and prints an ID (`eab3bc52…`). The VM boots; `Daemon has completed initialization` at 23:15:56. At 23:15:36 the backend logged `[idle][E] starting VM: … context deadline exceeded` and stayed in cache mode |
| 23:15:58 | `docker network ls`, `docker network inspect bridge` | **stale:** no `ablv-probe-1077`, `bridge` still `c62046ba…` (`cache << GET /v1.47/networks`) |
| 23:16:06 | `docker network inspect ablv-probe-1077` | `network ablv-probe-1077 not found` (`apicache 404 << GET …`), 8 s after the create succeeded |
| 23:16:23 | `docker network ls --filter name=ablv` | not cached: shows the network. The backend logs `engine … is already running`, `VM started` and leaves cache mode |
| 23:16:31 | `docker network ls`, `docker network inspect bridge` | consistent again: `bridge` is **`832efe0b…`**, created 23:15:56 |

So:
1. **New bridge ID:** the idle timer stops the VM, and the first uncached call starts a new
   `dockerd`. A snapshot (`docker ps`, `docker network ls`) alone does not wake it; the first
   write does, which is why the change showed up "during `services up`".
2. **"Create succeeds, the object is missing":** when the wake takes more than
   60 s, Docker Desktop keeps answering plain reads from the cache it took before the stop, while
   writes reach the new engine. Here that lasted 25 s and ended with the first uncached read. In
   that state an `inspect` by name answers "not found". This is **consistent with** the fourth
   report (2026-11-16 about 07:08 UTC, `DOCKER_FAILED No such container`), but does not prove it:
   no retained backend log covers that time, the report came right after an owner restart, and it
   describes a loss that lasted, where this one lasted 25 s. It was not reproduced through
   `services up`.

## Not explained

- **The recreated `Docker.raw`** (2026-11-16 07:30 UTC, 0 volumes and 1 image afterwards). The logs
  show Docker Desktop's error dialog opened twice at 07:27 UTC and the UI unable to reach the
  backend; they do not show why the disk was recreated. The disk is now 1 TiB (`DiskSizeMiB`);
  the fourth report measured 58.4 GB used at 81%, which suggests the size setting changed (an
  inference from two different measures). No AgentBreeder path touches it.
- **Why the VM did not wake** in the third report.
- **A container that disappeared** on 2026-11-15 (issue body): older than the retained logs.
- On 2026-11-18 Docker Desktop was quit at 08:14:50 UTC (`backend cancelled from …/handlers/app`)
  and started again at 23:01 UTC. Who did that is not in the logs. This session did neither.

## Is a restart during `services up` detected? No

`DockerCLI.run` (`src/agentbreeder/services/compose.py`) turns a failed Docker call into
`DOCKER_FAILED` with the last lines of stderr (a taken host port becomes `PORT_BUSY`, and
`authorize` raises `DOCKER_UNAVAILABLE`), and nothing compares the engine before and after.
A restart therefore surfaces as whatever failed next (`No such container`, `network … not found`).

Proposed follow-up (not in this PR; it needs a real-Docker test):
- `services/compose.py`: `DockerCLI.engine_epoch()` returns the default bridge network's ID, read
  with a **filtered** list (`docker network ls --no-trunc --filter name=bridge --filter
  driver=bridge`). A filtered list was not served from the cache above and it ended cache mode;
  `network inspect bridge` and `docker info` were served from it. The engine's `ID` in
  `docker info` is stored on the disk, so it should not change on a restart (not checked).
- `services/engine.py`, `Engine._up_confirmed`: read the epoch after `docker.authorize()` (this
  also wakes a stopped VM before the first write), and read it again when a Docker call fails and
  before `up` returns. If it changed or cannot be read, raise `DOCKER_RESTARTED` ("the Docker
  engine restarted during `services up`; run it again") instead of the later error.
- `docs/services.md`: Docker Desktop's Resource Saver stops the engine after 5 idle minutes; turn
  it off (Settings, Resources) on a machine that runs unattended, or expect the first command
  after an idle period to take a minute or more.

A restart from the idle timer in the middle of an `up` that already has a running container is
unlikely (the timer waits for no containers), but it was not tested.

## For the machine's owner

Turning Resource Saver off in Docker Desktop removes the new bridge IDs, the slow first command and
the stale reads. This session did not change any Docker setting.

## Leak scan and teardown

- No provider call was made. One key line was copied into a 0600 file in the sandbox `HOME` for
  the later tasks and deleted unused. This record was checked for home paths and key values.
- Docker before (23:05 UTC): 0 containers, 0 volumes, 3 images, networks `bridge`, `host`, `none`,
  66 build-cache entries (12.37 GB). After (23:17 UTC): the same containers, volumes, images and
  networks. The one network this session created, `ablv-probe-1077`, was removed by name. The
  default `bridge` ID changed from `c62046ba…` to `832efe0b…`, as shown above.
- The first `docker system df` after Docker Desktop started took about 130 s; other calls answered
  at once.
