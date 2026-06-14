import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { BuildFrontDoor } from "./BuildFrontDoor";

describe("BuildFrontDoor", () => {
  it("shows the prompt and example chips, and calls onStart with the chosen text", () => {
    const onStart = vi.fn();
    render(<BuildFrontDoor onStart={onStart} />);
    expect(screen.getByText(/what do you want to build/i)).toBeInTheDocument();
    fireEvent.click(screen.getByText(/support agent/i));
    expect(onStart).toHaveBeenCalledWith(expect.stringMatching(/support agent/i));
  });

  it("submits the freeform prompt", () => {
    const onStart = vi.fn();
    render(<BuildFrontDoor onStart={onStart} />);
    fireEvent.change(screen.getByTestId("frontdoor-input"), { target: { value: "an invoice bot" } });
    fireEvent.submit(screen.getByTestId("frontdoor-form"));
    expect(onStart).toHaveBeenCalledWith("an invoice bot");
  });
});
