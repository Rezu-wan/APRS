import axios, { AxiosError, AxiosInstance } from "axios";
import { clearSession, getSession } from "../lib/session";

/** Normalized API error — the only error type this app throws from the API layer. */
export class ApiError extends Error {
  readonly status: number | null;
  readonly code: string;
  /** Server-side correlation id (Stage 9): parsed from the error body's
   * `request_id`, falling back to the `X-Request-ID` response header. Used by
   * ErrorState so staff can quote it against server logs in a live demo. */
  readonly requestId?: string;

  constructor(status: number | null, code: string, message: string, requestId?: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.requestId = requestId;
  }
}

interface BackendErrorBody {
  error?: { code?: unknown; message?: unknown; request_id?: unknown };
}

export const apiClient: AxiosInstance = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000/api/v1",
  timeout: 15000,
});

apiClient.interceptors.request.use((config) => {
  // Don't overwrite an explicitly provided key (login flow before a session exists).
  const session = getSession();
  if (session?.apiKey && !config.headers.has("X-API-Key")) {
    config.headers.set("X-API-Key", session.apiKey);
  }
  return config;
});

/**
 * Stage 9: HTTP 429 is surfaced to the UI as a normal error (code RATE_LIMITED,
 * with the Retry-After value honored only by the server) — no automatic retry.
 * A silent retry would hide rate limiting from the demo; the user sees the
 * honest error and can retry manually.
 */
function normalizeError(err: unknown): ApiError {
  if (axios.isAxiosError(err)) {
    const axiosErr = err as AxiosError;

    if (axiosErr.code === "ECONNABORTED") {
      return new ApiError(null, "TIMEOUT", "The request timed out. Please try again.");
    }
    if (!axiosErr.response) {
      return new ApiError(
        null,
        "NETWORK_ERROR",
        "Cannot reach the server. Check your connection and try again."
      );
    }

    const status = axiosErr.response.status;

    if (status === 401) {
      // Only redirect when a session existed (mid-app expiry); a failed login
      // attempt must surface its error on the Login page instead of reloading it.
      const hadSession = getSession() !== null;
      clearSession();
      if (hadSession) {
        window.location.assign("/login");
      }
      return new ApiError(status, "UNAUTHORIZED", "Invalid API key");
    }

    const body = axiosErr.response.data as BackendErrorBody | undefined;
    const backendError = body?.error;
    // Correlation id: prefer the body's request_id; fall back to the response header.
    const headerRequestId = axiosErr.response.headers?.["x-request-id"];
    const requestId =
      backendError && typeof backendError === "object" && typeof backendError.request_id === "string"
        ? backendError.request_id
        : typeof headerRequestId === "string" && headerRequestId.length > 0
          ? headerRequestId
          : undefined;
    if (backendError && typeof backendError === "object") {
      // 429 always normalizes to RATE_LIMITED so the UI can show one honest message.
      const code =
        status === 429
          ? "RATE_LIMITED"
          : typeof backendError.code === "string"
            ? backendError.code
            : "UNKNOWN";
      return new ApiError(
        status,
        code,
        typeof backendError.message === "string"
          ? backendError.message
          : "The server returned an error.",
        requestId
      );
    }
    return new ApiError(
      status,
      status === 429 ? "RATE_LIMITED" : "UNKNOWN",
      "The server returned an error.",
      requestId
    );
  }
  return new ApiError(null, "UNKNOWN", "An unexpected error occurred.");
}

/** Exposed for tests: normalize a raw (axios-shaped) error into an ApiError. */
export { normalizeError };

/** Normalize any thrown error into an ApiError. Re-throwing from the interceptor chain. */
apiClient.interceptors.response.use(
  (response) => response,
  (error: unknown) => {
    const normalized = normalizeError(error);
    // 401 already redirected; still surface the error to callers.
    return Promise.reject(normalized);
  }
);

/**
 * Perform a GET with an explicit API key (used by login, before a session exists).
 * Bypasses the request interceptor's stored key.
 */
export async function getWithApiKey<T>(url: string, apiKey: string): Promise<T> {
  try {
    const response = await apiClient.get<T>(url, {
      headers: { "X-API-Key": apiKey },
    });
    return response.data;
  } catch (error) {
    if (error instanceof ApiError) throw error;
    throw normalizeError(error);
  }
}

export async function get<T>(url: string, config?: { params?: Record<string, unknown> }): Promise<T> {
  try {
    const response = await apiClient.get<T>(url, config);
    return response.data;
  } catch (error) {
    if (error instanceof ApiError) throw error;
    throw normalizeError(error);
  }
}

export async function post<T>(url: string, body?: unknown): Promise<T> {
  try {
    const response = await apiClient.post<T>(url, body);
    return response.data;
  } catch (error) {
    if (error instanceof ApiError) throw error;
    throw normalizeError(error);
  }
}

export async function patch<T>(url: string, body?: unknown): Promise<T> {
  try {
    const response = await apiClient.patch<T>(url, body);
    return response.data;
  } catch (error) {
    if (error instanceof ApiError) throw error;
    throw normalizeError(error);
  }
}
