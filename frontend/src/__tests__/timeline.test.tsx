import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { Timeline } from "../components/timeline/Timeline";
import { makeTimeline, makeTimelineEvent } from "../test/factories";

afterEach(() => cleanup());

describe("Timeline", () => {
  it("renders N events in API order with humanized labels and timestamps", () => {
    const events = [
      makeTimelineEvent(0, {
        event_type: "TRANSACTION_CREATED",
        timestamp: "2026-10-02T09:00:00Z",
        new_state: "INITIATED",
      }),
      makeTimelineEvent(1, {
        event_type: "STATE_TRANSITION",
        timestamp: "2026-10-02T09:05:00Z",
        previous_state: "INITIATED",
        new_state: "PROCESSING",
      }),
      makeTimelineEvent(2, {
        event_type: "STATE_TRANSITION",
        timestamp: "2026-10-02T09:10:00Z",
        previous_state: "PROCESSING",
        new_state: "RECOVERY_PENDING",
      }),
    ];

    render(<Timeline events={events} />);

    const list = screen.getByRole("list");
    expect(list).toBeInTheDocument();
    const items = screen.getAllByRole("listitem");
    expect(items).toHaveLength(3);

    // Humanized event-type labels (underscores -> spaces, capitalized).
    expect(screen.getByText("Transaction Created")).toBeInTheDocument();
    expect(screen.getAllByText("State Transition")).toHaveLength(2);

    // Timestamps are rendered as human-readable date-times (locale-dependent,
    // so assert on year presence per row rather than an exact string).
    expect(items[0].textContent).toContain("2026");
    expect(items[1].textContent).toContain("2026");
    expect(items[2].textContent).toContain("2026");

    // Order: first item is the created event, last is the newest state
    // (states are rendered raw as "prev → new").
    expect(items[0]).toHaveTextContent("Transaction Created");
    expect(items[2]).toHaveTextContent("RECOVERY_PENDING");
  });

  it("renders an em-dash placeholder for the first event's null previous_state", () => {
    render(<Timeline events={[makeTimelineEvent(0, { previous_state: null })]} />);
    const item = screen.getByRole("listitem");
    expect(item).toHaveTextContent("—");
    expect(item).toHaveTextContent(/→/);
  });

  it("shows the reason text when present", () => {
    render(
      <Timeline
        events={[
          makeTimelineEvent(0, {
            event_type: "RISK_ASSESSMENT",
            reason: "Gateway latency above threshold.",
          }),
        ]}
      />
    );
    expect(screen.getByText("Gateway latency above threshold.")).toBeInTheDocument();
  });

  it("shows an empty state when there are no events", () => {
    render(<Timeline events={[]} />);
    expect(screen.getByText(/no timeline events/i)).toBeInTheDocument();
    expect(screen.queryByRole("list")).not.toBeInTheDocument();
  });

  it("uses the factory-built timeline shape", () => {
    const response = makeTimeline(3);
    render(<Timeline events={response.events} />);
    expect(screen.getAllByRole("listitem")).toHaveLength(3);
  });
});
