// Stage 9 hardening: request-id correlation on errors, and the recovery
// panel's auditability footer line (truncated idempotency key + versions).

import "../test/mocks";

import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";
import type { AxiosError } from "axios";
import { ApiError, normalizeError } from "../api/client";
import { ErrorState } from "../components/ui/ErrorState";
import { AutonomousRecoveryPanel } from "../components/recovery/AutonomousRecoveryPanel";
import { makeRecovery } from "../test/factories";
import { mockGetRecovery, resetApiMocks } from "../test/mocks";
import { renderWithProviders } from "../test/utils";

beforeEach(() => {
  resetApiMocks();
});

/** Build a minimal axios-error-shaped object for normalizeError. */
function axiosLikeError(options: {
  status: number;
  body?: unknown;
  headers?: Record<string, string>;
}): AxiosError {
  return {
    isAxiosError: true,
    code: undefined,
    response: {
      status: options.status,
      data: options.body,
      headers: options.headers ?? {},
    },
  } as unknown as AxiosError;
}

describe("ApiError request-id correlation", () => {
  it("parses error.request_id from the body into requestId (429 -> RATE_LIMITED)", () => {
    const err = normalizeError(
      axiosLikeError({
        status: 429,
        body: { error: { code: "RATE_LIMITED", message: "Too many requests", request_id: "req-abc-123" } },
      }),
    );
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(429);
    expect(err.code).toBe("RATE_LIMITED");
    expect(err.message).toBe("Too many requests");
    expect(err.requestId).toBe("req-abc-123");
  });

  it("falls back to the X-Request-ID response header when the body has no request_id", () => {
    const err = normalizeError(
      axiosLikeError({
        status: 500,
        body: { error: { code: "UNKNOWN", message: "boom" } },
        headers: { "x-request-id": "hdr-xyz-789" },
      }),
    );
    expect(err.requestId).toBe("hdr-xyz-789");
  });

  it("leaves requestId undefined when neither body nor header carry one", () => {
    const err = normalizeError(
      axiosLikeError({ status: 500, body: { error: { code: "UNKNOWN", message: "boom" } } }),
    );
    expect(err.requestId).toBeUndefined();
  });
});

describe("ErrorState request-id line", () => {
  it("renders the Request ID line for an ApiError carrying a requestId", () => {
    render(
      <ErrorState message="boom" error={new ApiError(429, "RATE_LIMITED", "Too many requests", "req-abc-123")} />,
    );
    expect(screen.getByText("Request ID: req-abc-123")).toBeInTheDocument();
  });

  it("renders the line from the explicit requestId prop even without an ApiError", () => {
    render(<ErrorState message="boom" requestId="plain-id-1" />);
    expect(screen.getByText("Request ID: plain-id-1")).toBeInTheDocument();
  });

  it("hides the Request ID line when no correlation id is present", () => {
    render(
      <ErrorState
        message="boom"
        error={new ApiError(500, "UNKNOWN", "boom")}
        onRetry={() => {}}
      />,
    );
    expect(screen.queryByText(/^Request ID:/)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /try again/i })).toBeInTheDocument();
  });
});

describe("AutonomousRecoveryPanel auditability footer", () => {
  it("shows the truncated idempotency key and executor/verifier versions when present", async () => {
    mockGetRecovery.mockResolvedValue(makeRecovery());
    renderWithProviders(<AutonomousRecoveryPanel transactionId="TXN-1" />, {
      route: "/transactions/TXN-1",
      authUser: { role: "ADMIN", keyName: "api_key" },
    });

    const line = await screen.findByTestId("recovery-audit-line");
    // "a".repeat(64) truncated to a 12-char prefix.
    expect(line).toHaveTextContent("idempotency key aaaaaaaaaaaa…");
    expect(line).toHaveTextContent("executor v1");
    expect(line).toHaveTextContent("verifier v1");
    expect(line.textContent).toBe("idempotency key aaaaaaaaaaaa… · executor v1 · verifier v1");
  });

  it("hides the audit line for an old-shape record without the optional fields", async () => {
    mockGetRecovery.mockResolvedValue(
      makeRecovery({
        idempotency_key: null,
        risk_assessment_id: null,
        executor_version: null,
        verifier_version: null,
      }),
    );
    renderWithProviders(<AutonomousRecoveryPanel transactionId="TXN-1" />, {
      route: "/transactions/TXN-1",
      authUser: { role: "ADMIN", keyName: "api_key" },
    });

    expect(await screen.findByText("Verified")).toBeInTheDocument();
    expect(screen.queryByTestId("recovery-audit-line")).not.toBeInTheDocument();
  });
});
