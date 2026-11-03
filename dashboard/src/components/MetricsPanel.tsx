import type { DashboardMetrics } from "../types/api";
import { ErrorAlert } from "./ErrorAlert";
import {
  Activity,
  CheckCircle,
  AlertTriangle,
  Shield,
  Clock,
  Eye,
} from "lucide-react";

interface Props {
  metrics: DashboardMetrics | null;
  error?: string | null;
  loading?: boolean;
  onRetry?: () => void;
}

function StatCard({
  icon,
  label,
  value,
  color,
}: {
  icon: React.ReactNode;
  label: string;
  value: string | number;
  color: string;
}) {
  return (
    <div className="stat-card" role="group" aria-label={`${label}: ${value}`}>
      <div className="stat-icon" style={{ color }} aria-hidden="true">
        {icon}
      </div>
      <div className="stat-content">
        <span className="stat-value">{value}</span>
        <span className="stat-label">{label}</span>
      </div>
    </div>
  );
}

export function MetricsPanel({ metrics, error, loading, onRetry }: Props) {
  if (error) {
    return (
      <div className="panel metrics-panel" role="region" aria-label="System metrics">
        <h2>System Metrics</h2>
        <ErrorAlert message={error} onRetry={onRetry} />
      </div>
    );
  }

  if (loading && !metrics) {
    return (
      <div className="panel metrics-panel" role="region" aria-label="System metrics">
        <h2>Metrics</h2>
        <div className="stat-grid">
          {[...Array(6)].map((_, i) => (
            <div key={i} className="stat-card">
              <div className="skeleton" style={{ width: 20, height: 20 }} />
              <div className="stat-content">
                <span className="skeleton" style={{ width: 60, height: 24 }} />
                <span className="skeleton" style={{ width: 80, height: 14, marginTop: 4 }} />
              </div>
            </div>
          ))}
        </div>
      </div>
    );
  }

  if (!metrics) {
    return (
      <div className="panel metrics-panel" role="region" aria-label="System metrics">
        <h2>Metrics</h2>
        <p className="muted">Connecting to server...</p>
      </div>
    );
  }

  return (
    <div className="panel metrics-panel" role="region" aria-label="System metrics" aria-live="polite">
      <h2>System Metrics</h2>
      <div className="stat-grid">
        <StatCard
          icon={<Activity size={20} />}
          label="Total Events"
          value={metrics.total_events}
          color="#3b82f6"
        />
        <StatCard
          icon={<CheckCircle size={20} />}
          label="Corrections"
          value={metrics.corrections_made}
          color="#10b981"
        />
        <StatCard
          icon={<AlertTriangle size={20} />}
          label="Escalations"
          value={metrics.escalations}
          color="#f59e0b"
        />
        <StatCard
          icon={<Shield size={20} />}
          label="PII Detected"
          value={metrics.pii_detected}
          color="#ef4444"
        />
        <StatCard
          icon={<Eye size={20} />}
          label="Correction Rate"
          value={`${(metrics.correction_rate * 100).toFixed(1)}%`}
          color="#8b5cf6"
        />
        <StatCard
          icon={<Clock size={20} />}
          label="Avg Latency"
          value={`${metrics.avg_latency_ms.toFixed(0)}ms`}
          color="#06b6d4"
        />
      </div>
    </div>
  );
}
