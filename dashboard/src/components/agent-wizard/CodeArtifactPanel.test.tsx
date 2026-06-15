import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import { CodeArtifactPanel } from "./CodeArtifactPanel";

const files = { "agent.py": "print('hi')\n", "tools/search.py": "x = 1\n" };

describe("CodeArtifactPanel", () => {
  it("lists generated files", () => {
    render(<CodeArtifactPanel files={files} />);
    expect(screen.getByText("agent.py")).toBeInTheDocument();
    expect(screen.getByText("tools/search.py")).toBeInTheDocument();
  });

  it("shows file contents when a file is selected", () => {
    render(<CodeArtifactPanel files={files} />);
    fireEvent.click(screen.getByText("tools/search.py"));
    expect(screen.getByText(/x = 1/)).toBeInTheDocument();
  });

  it("renders an empty state when there are no files", () => {
    render(<CodeArtifactPanel files={{}} />);
    expect(screen.getByText(/No code generated yet/i)).toBeInTheDocument();
  });
});
