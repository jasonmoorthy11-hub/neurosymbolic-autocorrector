// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useAnalyticsRefresh } from "./useAnalyticsRefresh";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

interface Props {
  newestId: string | undefined;
  refresh: () => void;
  enabled: boolean;
}

function Probe({ newestId, refresh, enabled }: Props) {
  useAnalyticsRefresh(newestId, refresh, enabled, 2000);
  return null;
}

describe("useAnalyticsRefresh", () => {
  let root: Root;
  let container: HTMLDivElement;
  const refresh = vi.fn();

  const render = (props: Props) => act(() => root.render(<Probe {...props} />));

  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(100_000);
    refresh.mockReset();
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    vi.useRealTimers();
  });

  it("refreshes once per new event outside the gap", async () => {
    await render({ newestId: "a", refresh, enabled: true });
    expect(refresh).toHaveBeenCalledTimes(1);
    await render({ newestId: "a", refresh, enabled: true }); // same event: no second call
    expect(refresh).toHaveBeenCalledTimes(1);
    await act(() => vi.advanceTimersByTime(2500));
    await render({ newestId: "b", refresh, enabled: true });
    expect(refresh).toHaveBeenCalledTimes(2);
  });

  it("delays a burst's last event to the trailing edge rather than dropping it", async () => {
    await render({ newestId: "a", refresh, enabled: true });
    await act(() => vi.advanceTimersByTime(500));
    await render({ newestId: "b", refresh, enabled: true });
    await act(() => vi.advanceTimersByTime(500));
    await render({ newestId: "c", refresh, enabled: true });
    expect(refresh).toHaveBeenCalledTimes(1); // b and c both inside the gap
    await act(() => vi.advanceTimersByTime(999));
    expect(refresh).toHaveBeenCalledTimes(1);
    await act(() => vi.advanceTimersByTime(1));
    expect(refresh).toHaveBeenCalledTimes(2); // one trailing refresh at t+2000, not two
    await act(() => vi.advanceTimersByTime(5000));
    expect(refresh).toHaveBeenCalledTimes(2);
  });

  it("drops a pending trailing refresh on unmount", async () => {
    await render({ newestId: "a", refresh, enabled: true });
    await act(() => vi.advanceTimersByTime(500));
    await render({ newestId: "b", refresh, enabled: true });
    await act(() => root.unmount());
    await act(() => vi.advanceTimersByTime(5000));
    expect(refresh).toHaveBeenCalledTimes(1);
    root = createRoot(container); // for afterEach
  });

  it("never refreshes while disabled (replay mode) or without an event", async () => {
    await render({ newestId: undefined, refresh, enabled: true });
    await render({ newestId: "a", refresh, enabled: false });
    await act(() => vi.advanceTimersByTime(5000));
    expect(refresh).not.toHaveBeenCalled();
  });
});
