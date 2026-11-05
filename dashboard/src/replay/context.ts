import { createContext, useContext } from "react";
import type {
  AnalyticsSummary,
  CorrectionEvent,
  DashboardMetrics,
  EventFilters,
  RuleInfo,
  SupervisorQueueItem,
  TrendBucket,
} from "../types/api";
import type { ReplayFile, ReplayScenario } from "../types/replay";
import type { Lane } from "./lane";
import type { PlayerState, Speed } from "./player";

export interface ReplayValue {
  file: ReplayFile | null;
  error: string | null;
  loading: boolean;
  reload: () => void;
  /** Which recording is playing; switching resets the player and the decisions. */
  lane: Lane;
  setLane: (lane: Lane) => void;
  player: Pick<PlayerState, "playing" | "speed">;
  total: number;
  /** Scenarios revealed so far, in replay order. */
  visible: ReplayScenario[];
  /** Revealed scenarios as live-shaped events, newest first. */
  events: CorrectionEvent[];
  metrics: DashboardMetrics | null;
  queue: SupervisorQueueItem[];
  rules: RuleInfo[];
  trends: TrendBucket[];
  summary: AnalyticsSummary | null;
  /** The pinned scenario, or the newest one while following. */
  selected: ReplayScenario | null;
  following: boolean;
  select: (id: string | null) => void;
  play: () => void;
  pause: () => void;
  step: () => void;
  reset: () => void;
  setSpeed: (speed: Speed) => void;
  approve: (eventId: string) => Promise<void>;
  reject: (eventId: string, reviewer?: string, comment?: string, reason?: string | null) => Promise<void>;
  exportEvents: (format: "csv" | "json", filters?: EventFilters) => Promise<void>;
}

export const ReplayContext = createContext<ReplayValue | null>(null);

export function useReplayContext(): ReplayValue {
  const value = useContext(ReplayContext);
  if (!value) throw new Error("useReplayContext must be used inside ReplayProvider");
  return value;
}
