import { describe, expect, it } from "vitest";
import { describeJobError } from "./errors";

describe("describeJobError", () => {
  it("maps MissingApiKey to actionable human text", () => {
    const text = describeJobError("MissingApiKey");
    expect(text).toContain("No API key configured");
    expect(text).toContain(".env file");
    // Hygiene: must not leak or invite raw provider error details.
    expect(text).not.toContain("ValueError");
  });

  it("passes unknown categories through unchanged", () => {
    expect(describeJobError("RuntimeError")).toBe("RuntimeError");
    expect(describeJobError("Cancelled")).toBe("Cancelled");
    expect(describeJobError("ProviderLockTimeout")).toBe("ProviderLockTimeout");
  });
});
