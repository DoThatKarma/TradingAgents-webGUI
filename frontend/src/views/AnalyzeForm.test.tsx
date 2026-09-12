import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../api/client";
import { AnalyzeForm } from "./AnalyzeForm";

const fetchMock = vi.fn();

beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
  vi.stubEnv("VITE_API_TOKEN", "");
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
  fetchMock.mockReset();
});

async function fillValidForm() {
  await userEvent.type(screen.getByLabelText(/ticker/i), "NVDA");
  // date is prefilled with today (YYYY-MM-DD) — valid by default.
}

describe("AnalyzeForm instructions", () => {
  it("disables instructions for provider 'direct' and enables them for 'ta_plugins'", async () => {
    render(<AnalyzeForm onSubmit={vi.fn()} />);
    const textarea = screen.getByLabelText(/custom instructions/i) as HTMLTextAreaElement;
    expect(textarea.disabled).toBe(true);

    await userEvent.selectOptions(screen.getByLabelText(/^provider/i), "ta_plugins");
    expect((screen.getByLabelText(/custom instructions/i) as HTMLTextAreaElement).disabled).toBe(
      false,
    );
  });

  it("shows a live character counter and blocks submit above 4000 chars", async () => {
    render(<AnalyzeForm onSubmit={vi.fn()} />);
    await fillValidForm();
    await userEvent.selectOptions(screen.getByLabelText(/^provider/i), "ta_plugins");

    const textarea = screen.getByLabelText(/custom instructions/i) as HTMLTextAreaElement;
    const counter = screen.getByTestId("instructions-counter");
    expect(counter).toHaveTextContent("0/4000");

    fireEvent.change(textarea, { target: { value: "a".repeat(4001) } });
    expect(counter).toHaveTextContent("4001/4000");

    expect(screen.getByTestId("instructions-error")).toHaveTextContent(
      /exceed 4000/i,
    );
    expect(screen.getByTestId("submit-run")).toBeDisabled();
  });

  it("shows an inline 422 error when the backend rejects the run", async () => {
    const onSubmit = vi.fn().mockRejectedValue(new ApiError(422, "invalid run request"));
    render(<AnalyzeForm onSubmit={onSubmit} />);
    await fillValidForm();

    await userEvent.click(screen.getByTestId("submit-run"));

    await waitFor(() => {
      expect(screen.getByTestId("api-error")).toHaveTextContent(/invalid run request/i);
    });
    expect(onSubmit).toHaveBeenCalledTimes(1);
  });

  it("shows a capacity hint for 429 rejections", async () => {
    const onSubmit = vi.fn().mockRejectedValue(
      new ApiError(429, "run capacity reached; retry later", 5),
    );
    render(<AnalyzeForm onSubmit={onSubmit} />);
    await fillValidForm();

    await userEvent.click(screen.getByTestId("submit-run"));

    await waitFor(() => {
      expect(screen.getByTestId("api-error")).toHaveTextContent(/capacity reached/i);
    });
  });
});
