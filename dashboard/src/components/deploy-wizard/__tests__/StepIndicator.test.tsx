import { describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { StepIndicator } from "@/components/deploy-wizard/StepIndicator";

describe("StepIndicator", () => {
  it("renders 5 dots", () => {
    render(
      <StepIndicator current={1} canAdvanceTo={() => true} onJump={() => {}} />,
    );
    expect(screen.getAllByRole("button")).toHaveLength(5);
  });

  it("disables forward jump when canAdvanceTo returns false", () => {
    const onJump = vi.fn();
    render(
      <StepIndicator
        current={1}
        canAdvanceTo={(n) => n <= 1}
        onJump={onJump}
      />,
    );
    fireEvent.click(screen.getAllByRole("button")[3]); // jump to step 4 (blocked)
    expect(onJump).not.toHaveBeenCalled();
  });

  it("allows backwards jump when canAdvanceTo returns true for that step", () => {
    const onJump = vi.fn();
    render(
      <StepIndicator
        current={4}
        canAdvanceTo={(n) => n <= 4}
        onJump={onJump}
      />,
    );
    fireEvent.click(screen.getAllByRole("button")[1]); // jump back to step 2
    expect(onJump).toHaveBeenCalledWith(2);
  });

  it("clicking active step is a no-op but still dispatches", () => {
    const onJump = vi.fn();
    render(
      <StepIndicator current={3} canAdvanceTo={() => true} onJump={onJump} />,
    );
    fireEvent.click(screen.getAllByRole("button")[2]); // index 2 = step 3 = active
    // Allowed but is a no-op move (caller can ignore step===current dispatches).
    // We still call onJump(3) — the consumer decides whether to dispatch.
    expect(onJump).toHaveBeenCalledWith(3);
  });
});
