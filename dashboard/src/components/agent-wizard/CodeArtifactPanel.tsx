import { useState } from "react";

interface CodeArtifactPanelProps {
  files: Record<string, string>;
}

export function CodeArtifactPanel({ files }: CodeArtifactPanelProps) {
  const paths = Object.keys(files).sort();
  const [selected, setSelected] = useState<string | null>(paths[0] ?? null);

  if (paths.length === 0) {
    return (
      <div className="flex h-full items-center justify-center text-sm text-gray-500">
        No code generated yet. Use “Eject to code” to generate agent.py and tools.
      </div>
    );
  }

  const active = selected && files[selected] !== undefined ? selected : paths[0];

  return (
    <div className="flex h-full">
      <ul className="w-48 shrink-0 overflow-auto border-r border-gray-200 text-sm">
        {paths.map((p) => (
          <li key={p}>
            <button
              type="button"
              onClick={() => setSelected(p)}
              className={`block w-full truncate px-3 py-2 text-left hover:bg-gray-50 ${
                p === active ? "bg-gray-100 font-medium" : ""
              }`}
            >
              {p}
            </button>
          </li>
        ))}
      </ul>
      <pre className="flex-1 overflow-auto bg-gray-50 p-4 text-xs leading-relaxed">
        {files[active]}
      </pre>
    </div>
  );
}
