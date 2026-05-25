import { render, screen } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import { PageTitle } from "./page-title";

describe("PageTitle", () => {
  it("renders the title text in a heading", () => {
    render(<PageTitle>Models</PageTitle>);
    const h = screen.getByRole("heading", { level: 1, name: "Models" });
    expect(h).toBeInTheDocument();
  });

  it("applies the display font utility class", () => {
    render(<PageTitle>Models</PageTitle>);
    const h = screen.getByRole("heading", { level: 1 });
    expect(h.className).toContain("font-display");
  });

  it("renders an optional subtitle", () => {
    render(<PageTitle subtitle="0 models in registry">Models</PageTitle>);
    expect(screen.getByText("0 models in registry")).toBeInTheDocument();
  });
});
