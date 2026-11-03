import { useReplayContext } from "../replay/context";
import { AgentFeed } from "./AgentFeed";

/** The agent feed fed by the replay, with events selectable for the pipeline trace. */
export function ReplayFeed() {
  const { events, file, selected, following, select } = useReplayContext();
  const start = file ? Date.parse(file.recorded_at) : 0;

  return (
    <AgentFeed
      events={events}
      title="Agent Feed (recorded)"
      emptyMessage="Press Play to replay the recorded runs."
      selectedId={selected?.id ?? null}
      pinnedId={following ? null : (selected?.id ?? null)}
      onSelect={select}
      timeLabel={(e) => `+${((Date.parse(e.timestamp) - start) / 1000).toFixed(1)}s`}
    />
  );
}
