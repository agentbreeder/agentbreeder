# AgentBreeder Benchmarks

Performance benchmarks for AgentBreeder's critical paths. Results are stored as CI artifacts and committed baselines so regressions are caught before merge.

## What is benchmarked

| Benchmark | File | What it measures |
|-----------|------|-----------------|
| `test_benchmark_agent_yaml_parse` | `benchmark_core.py` | Parse `agent.yaml` → `AgentConfig` end-to-end |
| `test_benchmark_agent_yaml_validate` | `benchmark_core.py` | JSON Schema validation of `agent.yaml` |
| `test_benchmark_orchestration_yaml_parse` | `benchmark_core.py` | Parse `orchestration.yaml` → `OrchestrationConfig` |
| `test_benchmark_orchestration_yaml_validate` | `benchmark_core.py` | JSON Schema validation of `orchestration.yaml` |
| `test_benchmark_sdk_router_build` | `benchmark_core.py` | Build a router orchestration via Python SDK |
| `test_benchmark_sdk_pipeline_build` | `benchmark_core.py` | Build a 5-step sequential pipeline via SDK |
| `test_benchmark_sdk_fanout_build` | `benchmark_core.py` | Build a FanOut with 4 workers via SDK |
| `test_benchmark_sdk_supervisor_build` | `benchmark_core.py` | Build a Supervisor with 3 workers via SDK |
| `test_benchmark_sdk_yaml_roundtrip` | `benchmark_core.py` | SDK build → `to_yaml()` → `from_yaml()` round-trip |
| `test_benchmark_sdk_validate` | `benchmark_core.py` | Validate a complex orchestration config |
| `test_benchmark_yaml_safe_load` | `benchmark_core.py` | Raw `yaml.safe_load` baseline |

## Running benchmarks locally

### Prerequisites

```bash
pip install -e ".[dev]"
pip install pytest-benchmark
```

### Run all benchmarks

```bash
pytest benchmarks/ --benchmark-only
```

### Save a named baseline

```bash
pytest benchmarks/ --benchmark-save=v1.7.1
```

Baselines are stored in `.benchmarks/` (committed to git for release tags, ignored for local runs — see `.gitignore`).

### Compare against a saved baseline

```bash
pytest benchmarks/ --benchmark-compare=v1.7.1
```

### Compare against the last run

```bash
pytest benchmarks/ --benchmark-compare
```

### Output as JSON (for CI artifact storage)

```bash
pytest benchmarks/ --benchmark-json=benchmark-results.json
```

## Baseline results (v1.7.1 on GitHub Actions, ubuntu-latest, Python 3.12)

Results are generated on `ubuntu-latest` via GitHub Actions. Local results will vary by hardware.

| Benchmark | Mean | Std Dev | Min | Max |
|-----------|------|---------|-----|-----|
| `test_benchmark_yaml_safe_load` | 42 µs | 1.2 µs | 40 µs | 48 µs |
| `test_benchmark_agent_yaml_validate` | 180 µs | 8 µs | 170 µs | 210 µs |
| `test_benchmark_agent_yaml_parse` | 220 µs | 12 µs | 205 µs | 260 µs |
| `test_benchmark_orchestration_yaml_validate` | 195 µs | 9 µs | 182 µs | 230 µs |
| `test_benchmark_orchestration_yaml_parse` | 240 µs | 14 µs | 222 µs | 285 µs |
| `test_benchmark_sdk_router_build` | 85 µs | 3 µs | 81 µs | 94 µs |
| `test_benchmark_sdk_pipeline_build` | 78 µs | 2.8 µs | 74 µs | 87 µs |
| `test_benchmark_sdk_fanout_build` | 92 µs | 4 µs | 87 µs | 103 µs |
| `test_benchmark_sdk_supervisor_build` | 88 µs | 3.5 µs | 84 µs | 98 µs |
| `test_benchmark_sdk_validate` | 310 µs | 18 µs | 290 µs | 360 µs |
| `test_benchmark_sdk_yaml_roundtrip` | 460 µs | 22 µs | 430 µs | 520 µs |

> Baselines above were recorded on `ubuntu-latest` (GitHub Actions), Python 3.12, `pytest-benchmark 4.0.0`.
> Local results will differ — use `--benchmark-compare` against a baseline saved on the same machine for meaningful regression detection.

## Regression policy

CI uses `--benchmark-compare` against the most recent committed baseline and fails if any benchmark regresses by more than **20%** (mean). To update the baseline after an intentional performance change:

```bash
# Run locally and commit the new baseline
pytest benchmarks/ --benchmark-save=v<new-version>
git add .benchmarks/
git commit -m "benchmarks: update baseline to v<new-version>"
```

## Reproducing CI results exactly

CI benchmark runs use the following flags:

```bash
pytest benchmarks/ \
  --benchmark-only \
  --benchmark-json=benchmark-results.json \
  --benchmark-min-rounds=10 \
  --benchmark-warmup=on
```

Results are uploaded as a GitHub Actions artifact (`benchmark-results`) and retained for 30 days.
