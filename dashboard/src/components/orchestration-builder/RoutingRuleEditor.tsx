import { Plus, Trash2 } from "lucide-react";
import { Input } from "@/components/ui/input";

interface RoutingRule {
  condition: string;
  target: string;
}

interface RoutingRuleEditorProps {
  rules: RoutingRule[];
  onChange: (rules: RoutingRule[]) => void;
  availableTargets: string[];
}

export function RoutingRuleEditor({ rules, onChange, availableTargets }: RoutingRuleEditorProps) {
  const addRule = () => onChange([...rules, { condition: "", target: availableTargets[0] ?? "" }]);
  const removeRule = (idx: number) => onChange(rules.filter((_, i) => i !== idx));
  const updateRule = (idx: number, field: keyof RoutingRule, value: string) => {
    const updated = [...rules];
    updated[idx] = { ...updated[idx], [field]: value };
    onChange(updated);
  };

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <p className="text-sm font-medium">Routing Rules</p>
        <button className="flex items-center gap-1 text-xs text-primary hover:underline" onClick={addRule}>
          <Plus className="size-3" /> Add Rule
        </button>
      </div>
      {rules.map((rule, i) => (
        <div key={i} className="flex items-center gap-2">
          <Input
            className="flex-1"
            placeholder="Condition (keyword)"
            value={rule.condition}
            onChange={(e) => updateRule(i, "condition", e.target.value)}
          />
          <select
            className="rounded border bg-background px-2 py-1.5 text-sm"
            value={rule.target}
            onChange={(e) => updateRule(i, "target", e.target.value)}
          >
            {availableTargets.map((t) => <option key={t} value={t}>{t}</option>)}
          </select>
          <button className="text-muted-foreground hover:text-red-500" onClick={() => removeRule(i)}>
            <Trash2 className="size-4" />
          </button>
        </div>
      ))}
      {rules.length === 0 && <p className="text-xs text-muted-foreground">No rules — add conditions to route messages</p>}
    </div>
  );
}
