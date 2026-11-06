import { useMemo } from "react";
import { useReplayContext } from "../replay/context";
import { filterEvents } from "../replay/derive";
import type { EventFilters } from "../types/api";
import { AgentFeed } from "./AgentFeed";

interface Props {
  /** The filter bar's state; the export applies the same filter. */
  filters?: EventFilters;
}

/** The agent feed fed by the replay, with events selectable for the pipeline trace. */
export function ReplayFeed({ filters }: Props) {
  const { events, file, selected, following, select } = useReplayContext();
  const start = file ? Date.parse(file.recorded_at) : 0;
  const shown = useMemo(() => (filters ? filterEvents(events, filters) : events), [events, filters]);

  return (
    <AgentFeed
      events={shown}
      title="Agent Feed (recorded)"
      emptyMessage="Press Play to replay the recorded runs."
      selectedId={selected?.id ?? null}
      pinnedId={following ? null : (selected?.id ?? null)}
      onSelect={select}
      timeLabel={(e) => `+${((Date.parse(e.timestamp) - start) / 1000).toFixed(1)}s`}
    />
  );
}
