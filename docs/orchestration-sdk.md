# Full Code Orchestration SDK

The Full Code Orchestration SDK is the third tier of AgentBreeder's Three-Tier Builder Model. It lets you define complex multi-agent workflows programmatically — for cases that `orchestration.yaml` can't express.

```
No Code (Canvas)  ──→  orchestration.yaml  ──→  Python/TypeScript SDK
                              ↑                          ↓
                              └──── all compile to ──────┘
                                    same deploy pipeline
```

**Available as:** `from agenthub import Orchestration, Pipeline, FanOut, Supervisor`
**TypeScript:** `import { Orchestration, Pipeline, FanOut, Supervisor } from "@agentbreeder/sdk"`

---

## When to Use the SDK vs YAML

| You need… | Use |
|-----------|-----|
| Standard routing, sequential, parallel, supervisor strategies | `orchestration.yaml` or Canvas |
| Business rules in routing (e.g., "VIP users get a different agent") | SDK + custom `Router` |
| Dynamic agent selection at runtime | SDK |
| Human-in-the-loop breakpoints | SDK (post-v1.3) |
| Stateful workflows that YAML can't express | SDK |
| Reuse across multiple projects as a Python/TS module | SDK |

---

## Classes

### `Orchestration`

Base builder for any strategy. Use directly for `router`, `parallel`, or `hierarchical` strategies.

```python
from agenthub import Orchestration

pipeline = (
    Orchestration("support-pipeline", strategy="router", team="eng", version="1.0.0")
    .add_agent("triage",  ref="agents/triage-agent")
    .add_agent("billing", ref="agents/billing-agent")
    .add_agent("general", ref="agents/general-agent")
    .with_route("triage", condition="billing",  target="billing")
    .with_route("triage", condition="default",  target="general")
    .with_shared_state(state_type="session_context", backend="redis")
    .with_deploy(target="cloud-run")
    .tag("production", "support")
)

errors = pipeline.validate()   # [] if valid
yaml_str = pipeline.to_yaml()  # valid orchestration.yaml string
pipeline.save("orchestration.yaml")
pipeline.deploy()
```

**Builder methods:**

| Method | Description |
|--------|-------------|
| `add_agent(name, ref, fallback=None)` | Add an agent reference |
| `with_route(agent, condition, target)` | Add a routing rule to an agent |
| `with_shared_state(state_type, backend)` | Configure inter-agent shared state |
| `with_supervisor(supervisor_agent, merge_agent, max_iterations)` | Configure supervisor |
| `with_deploy(target, **resources)` | Set deployment target + resource limits |
| `tag(*tags)` | Add discovery tags |
| `validate()` | Returns `list[str]` — empty means valid |
| `to_yaml()` | Serialize to `orchestration.yaml` string |
| `from_yaml(yaml_str)` | Class method — parse YAML into correct subclass |
| `from_file(path)` | Class method — load from file |
| `save(path)` | Write `orchestration.yaml` to disk |
| `deploy(target=None)` | Trigger deployment via engine pipeline |

---

### `Pipeline`

Sequential agent chain: each step's output feeds the next step's input.

```python
from agenthub import Pipeline

research = (
    Pipeline("research-pipeline", team="eng")
    .step("researcher", ref="agents/researcher")
    .step("summarizer", ref="agents/summarizer")
    .step("reviewer",   ref="agents/reviewer", fallback="summarizer")
    .with_deploy(target="cloud-run")
)
```

**Extra method:** `.step(name, ref, fallback=None)` — appends an agent to the chain.
**Validation:** Requires at least 2 steps.

---

### `FanOut`

Fan-out to multiple parallel agents, then merge all outputs with a merge agent.

```python
from agenthub import FanOut

analysis = (
    FanOut("multi-analysis", team="eng")
    .worker("sentiment",  ref="agents/sentiment-analyzer")
    .worker("topics",     ref="agents/topic-extractor")
    .worker("summary",    ref="agents/summarizer")
    .merge(ref="agents/aggregator", name="merger")   # default name: "merger"
    .with_merge_strategy("aggregate")                # first_wins | majority_vote | aggregate | custom
)
```

**Extra methods:**
- `.worker(name, ref)` — add a parallel worker
- `.merge(ref, name="merger")` — set the merge agent
- `.with_merge_strategy(strategy)` — `"first_wins"`, `"majority_vote"`, `"aggregate"`, `"custom"`

**Validation:** Requires a merge agent.

---

### `Supervisor`

Hierarchical orchestration: a supervisor agent reads the task, delegates to workers, and synthesises results.

```python
from agenthub import Supervisor

workflow = (
    Supervisor("research-workflow", team="eng")
    .with_supervisor_agent("coordinator", ref="agents/coordinator")
    .worker("researcher", ref="agents/researcher")
    .worker("writer",     ref="agents/writer")
    .worker("reviewer",   ref="agents/reviewer", fallback="writer")
    .with_max_iterations(5)
)
```

**Extra methods:**
- `.with_supervisor_agent(name, ref)` — set the supervisor
- `.worker(name, ref, fallback=None)` — add a worker
- `.with_max_iterations(n)` — limit supervisor/worker loop

**Validation:** Requires `with_supervisor_agent()` to be called.

---

## Router Classes

Use routers when YAML `condition:` keywords aren't expressive enough.

### `KeywordRouter`

Routes on keyword presence in the message.

```python
from agenthub import KeywordRouter, Orchestration

router = KeywordRouter(
    rules={
        "billing": "billing-agent",
        "refund":  "billing-agent",
        "broken":  "tech-support",
    },
    default="general-agent",
    case_sensitive=False,  # default
)
# result = await router.route(message, context)
```

### `IntentRouter`

Routes on `context["intent"]` — use after a classifier step sets the intent.

```python
from agenthub import IntentRouter

router = IntentRouter(
    intents={
        "billing_inquiry":  "billing-agent",
        "technical_support": "tech-agent",
        "general_question":  "general-agent",
    },
    default="general-agent",
)
```

### `RoundRobinRouter`

Distributes messages evenly across agents in order.

```python
from agenthub import RoundRobinRouter

router = RoundRobinRouter(agents=["agent-a", "agent-b", "agent-c"])
```

### `ClassifierRouter` (base class for model-based routing)

Override `classify()` to implement LLM or ML-based intent detection.

```python
from agenthub import ClassifierRouter

class SupportClassifier(ClassifierRouter):
    async def classify(self, message: str) -> str:
        # Call your model or classifier
        response = await my_llm.invoke(f"Classify: {message}\nLabels: billing, technical, general")
        return response.strip()

    async def route(self, message: str, context: dict) -> str:
        intent = await self.classify(message)
        # Add business rules on top of classification
        if intent == "billing" and context.get("user_tier") == "enterprise":
            return "priority-billing-agent"
        return self.label_to_agent.get(intent, self.default)

router = SupportClassifier(
    label_to_agent={"billing": "billing-agent", "technical": "tech-agent"},
    default="general-agent",
)
```

---

## YAML Round-Trip

All SDK classes produce valid `orchestration.yaml` that is identical to hand-written YAML:

```python
# Build in code
pipeline = (
    Pipeline("research", team="eng")
    .step("researcher", ref="agents/researcher")
    .step("summarizer", ref="agents/summarizer")
)

# Save as YAML — can be opened and edited in the Dashboard
pipeline.save("orchestration.yaml")

# Load back — get the correct subclass (Pipeline, not base Orchestration)
loaded = Orchestration.from_file("orchestration.yaml")
assert isinstance(loaded, Pipeline)
assert loaded.config.name == "research"
```

---

## TypeScript SDK

The TypeScript SDK has an identical API surface with TypeScript-native types:

```typescript
import { Orchestration, Pipeline, FanOut, Supervisor, KeywordRouter } from "@agentbreeder/sdk";

const support = new Orchestration("support-pipeline", "router", { team: "eng" })
  .addAgent("triage",  "agents/triage")
  .addAgent("billing", "agents/billing")
  .addAgent("general", "agents/general")
  .withRoute("triage", "billing", "billing")
  .withRoute("triage", "default", "general")
  .withSharedState("session_context", "redis");

const errors = support.validate();  // string[]
const yaml = support.toYaml();      // orchestration.yaml string
const result = support.deploy();    // { status: "pending", ... }

// Pipeline
const research = new Pipeline("research", { team: "eng" })
  .step("researcher", "agents/researcher")
  .step("summarizer", "agents/summarizer");

// FanOut
const analysis = new FanOut("analysis")
  .worker("sentiment", "agents/sentiment")
  .worker("topics",    "agents/topics")
  .merge("agents/aggregator");

// Supervisor
const workflow = new Supervisor("workflow")
  .withSupervisorAgent("coordinator", "agents/coordinator")
  .worker("researcher", "agents/researcher")
  .withMaxIterations(5);
```

---

## Relationship to `orchestration.yaml`

All SDK classes compile to the same `orchestration.yaml` format used by the Low Code tier:

| SDK Class | YAML `strategy` |
|-----------|----------------|
| `Orchestration("...", "router")` | `router` |
| `Orchestration("...", "parallel")` | `parallel` |
| `Pipeline` | `sequential` |
| `FanOut` | `fan_out_fan_in` |
| `Supervisor` | `supervisor` |

The deploy pipeline does not know which tier produced the config — governance, cost tracking, audit trail, and registry work identically for all three tiers.

---

## Eject from YAML to SDK

```bash
# Generate Python SDK from existing orchestration.yaml
agentbreeder eject orchestration.yaml

# Generate TypeScript SDK
agentbreeder eject orchestration.yaml --sdk typescript
```

---

*See also: [orchestration.yaml reference](orchestration-yaml.md) · [CLI reference](cli-reference.md#agentbreeder-orchestration)*
