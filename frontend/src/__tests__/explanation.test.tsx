import "../test/mocks";

import { cleanup, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { ExplanationCard } from "../components/explanation/ExplanationCard";
import { makeExplanation } from "../test/factories";
import {
  apiError,
  mockRequestExplanation,
  neverPromise,
  resetApiMocks,
} from "../test/mocks";
import { renderWithProviders } from "../test/utils";

afterEach(() => cleanup());
beforeEach(() => {
  resetApiMocks();
  sessionStorage.clear();
});

describe("ExplanationCard", () => {
  it("requests a Bangla customer explanation for a CUSTOMER and renders the Bangla text", async () => {
    mockRequestExplanation.mockResolvedValue(
      makeExplanation({ explanation: "আপনার লিমিট পুনরায় চালু করা হয়েছে।" })
    );
    renderWithProviders(<ExplanationCard transactionId="TXN-1" />, {
      authUser: { role: "CUSTOMER", keyName: "cust-key" },
    });

    await userEvent.click(await screen.findByRole("button", { name: /generate explanation/i }));

    await waitFor(() => {
      expect(mockRequestExplanation).toHaveBeenCalledTimes(1);
    });
    expect(mockRequestExplanation).toHaveBeenCalledWith({
      transactionId: "TXN-1",
      language: "bn",
      audience: "customer",
    });

    expect(await screen.findByText("আপনার লিমিট পুনরায় চালু করা হয়েছে।")).toBeInTheDocument();
    // CUSTOMER cannot toggle language or audience.
    expect(screen.queryByRole("group", { name: /explanation language/i })).not.toBeInTheDocument();
    expect(
      screen.queryByLabelText(/explanation audience/i)
    ).not.toBeInTheDocument();
  });

  it("sends English + support when an ADMIN toggles both controls", async () => {
    mockRequestExplanation.mockResolvedValue(makeExplanation({ language: "en", audience: "support" }));
    renderWithProviders(<ExplanationCard transactionId="TXN-1" />, {
      authUser: { role: "ADMIN", keyName: "admin-key" },
    });

    await userEvent.click(await screen.findByRole("button", { name: "English" }));
    await userEvent.selectOptions(screen.getByLabelText(/explanation audience/i), "support");
    await userEvent.click(screen.getByRole("button", { name: /generate explanation/i }));

    await waitFor(() => {
      expect(mockRequestExplanation).toHaveBeenCalledWith({
        transactionId: "TXN-1",
        language: "en",
        audience: "support",
      });
    });
  });

  it("shows the 'Standard explanation' indicator when is_fallback is true", async () => {
    mockRequestExplanation.mockResolvedValue(makeExplanation({ is_fallback: true }));
    renderWithProviders(<ExplanationCard transactionId="TXN-1" />, {
      authUser: { role: "ADMIN", keyName: "admin-key" },
    });

    await userEvent.click(await screen.findByRole("button", { name: /generate explanation/i }));

    expect(await screen.findByText("Standard explanation")).toBeInTheDocument();
  });

  it("hides the fallback indicator when is_fallback is false", async () => {
    mockRequestExplanation.mockResolvedValue(makeExplanation({ is_fallback: false }));
    renderWithProviders(<ExplanationCard transactionId="TXN-1" />, {
      authUser: { role: "ADMIN", keyName: "admin-key" },
    });

    await userEvent.click(await screen.findByRole("button", { name: /generate explanation/i }));

    expect(await screen.findByText("আপনার লিমিট পুনরায় চালু করা হয়েছে।")).toBeInTheDocument();
    expect(screen.queryByText("Standard explanation")).not.toBeInTheDocument();
  });

  it("shows a friendly error banner without leaking provider internals", async () => {
    mockRequestExplanation.mockRejectedValue(
      apiError(502, "UPSTREAM_FAILURE", "Could not generate the explanation right now.")
    );
    const { container } = renderWithProviders(<ExplanationCard transactionId="TXN-1" />, {
      authUser: { role: "ADMIN", keyName: "admin-key" },
    });

    await userEvent.click(await screen.findByRole("button", { name: /generate explanation/i }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/could not generate/i);

    const html = container.innerHTML;
    expect(html).not.toContain("OPENAI_API_KEY");
    expect(html).not.toContain("sk-");
  });

  it("disables the button and shows 'Generating…' while the request is pending", async () => {
    mockRequestExplanation.mockReturnValue(neverPromise());
    renderWithProviders(<ExplanationCard transactionId="TXN-1" />, {
      authUser: { role: "ADMIN", keyName: "admin-key" },
    });

    const button = await screen.findByRole("button", { name: /generate explanation/i });
    await userEvent.click(button);

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /generating/i })).toBeDisabled();
    });
  });
});
