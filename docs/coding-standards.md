> Extracted from CLAUDE.md (thin-router refactor). Read on demand — see CLAUDE.md router for when.

## ✅ Coding Standards

### Python

```python
# Always use type hints
def deploy_agent(config: AgentConfig, env: str = "production") -> DeployResult:
    ...

# Always use Pydantic for data validation
class AgentConfig(BaseModel):
    name: str
    version: str
    team: str
    framework: FrameworkType
    model: ModelConfig
    deploy: DeployConfig

# Never use print() — use the logger
import logging
logger = logging.getLogger(__name__)
logger.info("Deploying agent", extra={"agent": config.name, "env": env})

# Always handle errors explicitly — never bare except
try:
    result = deployer.deploy(config)
except DeploymentError as e:
    logger.error("Deployment failed", extra={"error": str(e)})
    raise

# Async for all I/O
async def register_agent(agent: Agent) -> RegistryEntry:
    async with db.session() as session:
        ...
```

### TypeScript / React

```typescript
// Always type everything — no `any`
interface AgentCardProps {
  agent: Agent;
  onSelect: (id: string) => void;
}

// Use React Query for all API calls
const { data: agents, isLoading } = useQuery({
  queryKey: ['agents', teamId],
  queryFn: () => api.agents.list({ teamId }),
});

// Use Tailwind — no inline styles
// ✅
<div className="flex items-center gap-3 rounded-lg bg-white border border-gray-200 p-4">
// ❌
<div style={{ display: 'flex', padding: 16 }}>

// Always handle loading and error states
if (isLoading) return <Skeleton />;
if (error) return <ErrorBanner message={error.message} />;
```

### Tests

Every new feature requires:
- Unit test for the core logic (`tests/unit/`)
- Integration test for API endpoints (`tests/integration/`)
- E2E test if it touches the dashboard (`tests/e2e/`)

```python
# Unit test example — mock all external dependencies
async def test_deploy_validates_rbac_before_building():
    config = make_agent_config(team="engineering")
    rbac = MockRBAC(deny_team="engineering")
    engine = DeployEngine(rbac=rbac, builder=MockBuilder())

    with pytest.raises(RBACDeniedError):
        await engine.deploy(config, user="alice")

    # Builder should never have been called
    assert not MockBuilder.build_called
```

---
