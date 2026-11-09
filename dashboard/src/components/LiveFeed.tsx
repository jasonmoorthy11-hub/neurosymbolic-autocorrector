import { useMemo } from "react";
import { filterEvents } from "../replay/derive";
import type { CorrectionEvent, EventFilters } from "../types/api";
import { AgentFeed } from "./AgentFeed";

interface Props {
  events: CorrectionEvent[];
  filters: EventFilters;
}

/**
 * The live WebSocket feed, narrowed by the filter bar (the export uses the same
 * filter). It holds only the events received since this page connected (plus the
 * few the backend replays on connect), while the metrics and the export cover the
 * whole audit log, so the empty state says where the history is.
 */
export function LiveFeed({ events, filters }: Props) {
  const shown = useMemo(() => filterEvents(events, filters), [events, filters]);
  return (
    <AgentFeed
      events={shown}
      emptyMessage={events.length > 0 ? "No received events match the filters." : undefined}
      emptyNote="Showing events since this page connected; earlier ones are in the CSV and JSON export."
    />
  );
}
