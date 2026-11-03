import { useState } from "react";
import { Play, Square } from "lucide-react";
import { startDemo, stopDemo } from "../hooks/useApi";

interface Props {
  connected: boolean;
}

export function DemoControls({ connected }: Props) {
  const [running, setRunning] = useState(false);

  const handleStart = async () => {
    try {
      await startDemo(3.0);
      setRunning(true);
    } catch {
      // server not reachable
    }
  };

  const handleStop = async () => {
    try {
      await stopDemo();
      setRunning(false);
    } catch {
      // server not reachable
    }
  };

  return (
    <div className="demo-controls">
      <div className="connection-status">
        <span
          className={`status-dot ${connected ? 'status-connected' : 'status-disconnected'}`}
          style={{ backgroundColor: connected ? "#10b981" : "#ef4444" }}
        />
        {connected ? "Connected" : "Disconnected"}
      </div>
      {running ? (
        <button className="btn btn-stop" onClick={handleStop}>
          <Square size={14} /> Stop Demo
        </button>
      ) : (
        <button className="btn btn-start" onClick={handleStart}>
          <Play size={14} /> Start Demo
        </button>
      )}
    </div>
  );
}
