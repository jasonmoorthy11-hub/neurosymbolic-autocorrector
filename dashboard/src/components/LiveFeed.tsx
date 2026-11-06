import { useMemo } from "react";
import { filterEvents } from "../replay/derive";
import type { CorrectionEvent, EventFilters } from "../types/api";
import { AgentFeed } from "./AgentFeed";

interface Props {
  events: CorrectionEvent[];
  filters: EventFilters;
}

/** The live WebSocket feed, narrowed by the filter bar (the export uses the same filter). */
export function LiveFeed({ events, filters }: Props) {
  const shown = useMemo(() => filterEvents(events, filters), [events, filters]);
  return <AgentFeed events={shown} />;
}
