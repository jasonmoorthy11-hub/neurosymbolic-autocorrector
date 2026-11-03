import { Pause, Play, RotateCcw, SkipForward } from "lucide-react";
import { useReplayContext } from "../replay/context";
import type { Speed } from "../replay/player";

const SPEEDS: Speed[] = [1, 2, 4];

export function ReplayControls() {
  const { player, visible, total, file, play, pause, step, reset, setSpeed } = useReplayContext();
  const done = total > 0 && visible.length >= total;
  const disabled = !file;

  return (
    <div className="demo-controls replay-controls" role="group" aria-label="Replay controls">
      <span className="replay-progress" aria-live="polite">
        {visible.length} / {total} runs
      </span>
      {player.playing ? (
        <button type="button" className="btn btn-stop" onClick={pause} aria-label="Pause replay">
          <Pause size={14} aria-hidden="true" /> Pause
        </button>
      ) : (
        <button
          type="button"
          className="btn btn-start"
          onClick={play}
          disabled={disabled}
          aria-label={done ? "Play replay again from the start" : "Play replay"}
        >
          <Play size={14} aria-hidden="true" /> {done ? "Replay" : "Play"}
        </button>
      )}
      <button
        type="button"
        className="btn btn-secondary"
        onClick={disabled || done ? undefined : step}
        // aria-disabled, not disabled: a disabled button would drop keyboard focus at the end
        aria-disabled={disabled || done}
        aria-label="Step: show the next recorded run"
      >
        <SkipForward size={14} aria-hidden="true" /> Step
      </button>
      <button
        type="button"
        className="btn btn-secondary"
        onClick={reset}
        disabled={disabled}
        aria-label="Reset replay and supervisor decisions"
      >
        <RotateCcw size={14} aria-hidden="true" /> Reset
      </button>
      <label className="replay-speed">
        <span className="sr-only">Replay speed</span>
        <select
          value={player.speed}
          onChange={(e) => setSpeed(Number(e.target.value) as Speed)}
          aria-label="Replay speed"
        >
          {SPEEDS.map((s) => (
            <option key={s} value={s}>
              {s}×
            </option>
          ))}
        </select>
      </label>
    </div>
  );
}
