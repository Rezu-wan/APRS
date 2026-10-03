import { useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { ApiError } from "../api/client";
import { requestStreamTicket, supportStreamUrl } from "../api/support";

export type SupportStreamStatus =
  | "connecting"
  | "live"
  | "reconnecting"
  | "error";

/** Twin-state events (published by transaction_service on every committed
 * hop) vs case events (support_service). Both arrive on one stream. */
const TRANSACTION_EVENT_TYPES = new Set([
  "TRANSACTION_CREATED",
  "PAYMENT_PROCESSING",
  "PAYMENT_SUCCEEDED",
  "PAYMENT_FAILED",
  "PAYMENT_STALLED",
  "ML_RISK_ASSESSED",
  "RECOVERY_CHECKED",
  "LIMIT_RELEASED",
  "MANUAL_REVIEW_TRIGGERED",
  "RECOVERY_REJECTED",
]);

const MAX_BACKOFF_MS = 30_000;

interface StreamPayload {
  event_type?: unknown;
  transaction_id?: unknown;
}

/**
 * Live support feed: exchanges the session's API key for a single-use ticket,
 * opens the SSE stream, and invalidates react-query caches as REAL backend
 * events arrive. No polling, no synthetic timers for data.
 *
 * Disconnect handling: EventSource cannot send headers, so a drop means the
 * consumed ticket makes even auto-reconnect 401 — we close and rebuild from a
 * fresh ticket with exponential backoff (1s→30s). Permission errors stop the
 * loop honestly as "error".
 */
export function useSupportStream(enabled: boolean): SupportStreamStatus {
  const [status, setStatus] = useState<SupportStreamStatus>(
    enabled ? "connecting" : "error"
  );
  const queryClient = useQueryClient();
  const statusRef = useRef(status);
  statusRef.current = status;

  useEffect(() => {
    if (!enabled) {
      setStatus("error");
      return;
    }

    let cancelled = false;
    let source: EventSource | null = null;
    let ticketController: AbortController | null = null;
    let retryTimer: ReturnType<typeof setTimeout> | null = null;
    let backoffMs = 1000;

    const invalidateFor = (payload: StreamPayload) => {
      const eventType = typeof payload.event_type === "string" ? payload.event_type : "";
      // one prefix invalidates the whole support workspace (see queryKeys)
      void queryClient.invalidateQueries({ queryKey: ["support"] });
      if (TRANSACTION_EVENT_TYPES.has(eventType)) {
        const txId = typeof payload.transaction_id === "string" ? payload.transaction_id : null;
        if (txId) {
          void queryClient.invalidateQueries({ queryKey: ["transaction", txId] });
          void queryClient.invalidateQueries({ queryKey: ["timeline", txId] });
        }
      }
    };

    const teardown = () => {
      if (source) {
        source.close();
        source = null;
      }
      if (ticketController) {
        ticketController.abort();
        ticketController = null;
      }
    };

    const scheduleRetry = (why: string) => {
      teardown();
      if (cancelled) return;
      console.warn(`[support-stream] ${why}; retrying in ${Math.round(backoffMs / 1000)}s`);
      setStatus("reconnecting");
      retryTimer = setTimeout(connect, backoffMs);
      backoffMs = Math.min(backoffMs * 2, MAX_BACKOFF_MS);
    };

    const connect = () => {
      if (cancelled) return;
      setStatus((s) => (s === "live" ? "live" : "connecting"));
      ticketController = new AbortController();
      requestStreamTicket()
        .then((ticketResp) => {
          if (cancelled) return;
          source = new EventSource(supportStreamUrl(ticketResp.ticket));
          source.onopen = () => {
            backoffMs = 1000; // healthy connection resets the backoff ladder
            setStatus("live");
          };
          source.onmessage = (message: MessageEvent<string>) => {
            try {
              invalidateFor(JSON.parse(message.data) as StreamPayload);
            } catch (error) {
              // a malformed frame is logged, never swallowed — but it must
              // not kill the stream (heartbeat comment frames never hit
              // onmessage; only real data frames do)
              console.warn("[support-stream] unparseable event frame", error);
            }
          };
          source.onerror = () => scheduleRetry("stream dropped");
        })
        .catch((error: unknown) => {
          if (cancelled) return;
          if (error instanceof ApiError && (error.status === 401 || error.status === 403)) {
            // role revoked or session expired mid-session — retrying would
            // just hammer the endpoint; surface the honest stopped state
            console.warn("[support-stream] permission denied for stream:", error.message);
            teardown();
            setStatus("error");
            return;
          }
          scheduleRetry(error instanceof Error ? `ticket request failed: ${error.message}` : "ticket request failed");
        });
    };

    connect();
    return () => {
      cancelled = true;
      if (retryTimer) clearTimeout(retryTimer);
      teardown();
    };
  }, [enabled, queryClient]);

  return status;
}
