import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { DecisionCard } from "./DecisionCard";

describe("DecisionCard", () => {
  it("renders BUY signal with decision text", () => {
    render(<DecisionCard signal="BUY" decision="Increase position to 5%." />);
    expect(screen.getByTestId("decision-signal")).toHaveTextContent("BUY");
    expect(screen.getByText("Increase position to 5%.")).toBeInTheDocument();
  });

  it("renders REVIEW signals", () => {
    render(
      <DecisionCard signal="REVIEW: conflict" decision="Manual review advised." />,
    );
    const badge = screen.getByTestId("decision-signal");
    expect(badge).toHaveTextContent(/review/i);
    expect(badge.className).toContain("amber");
  });

  it("renders empty decision text with a fallback note", () => {
    render(<DecisionCard signal="HOLD" decision="" />);
    expect(screen.getByTestId("decision-signal")).toHaveTextContent("HOLD");
    expect(screen.getByText(/no decision text provided/i)).toBeInTheDocument();
  });

  it("does not interpret HTML in untrusted decision text", () => {
    render(<DecisionCard signal="BUY" decision="<img src=x onerror=alert(1)>" />);
    // React escaping: the payload renders as text, no <img> element exists.
    expect(screen.queryByRole("img")).toBeNull();
    expect(document.querySelector("img")).toBeNull();
  });
});
