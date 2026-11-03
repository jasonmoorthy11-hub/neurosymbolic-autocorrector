import { Download } from "lucide-react";
import type { EventFilters } from "../types/api";
import { useExport } from "../hooks/useApi";

interface Props {
  filters: EventFilters;
  onChange: (filters: EventFilters) => void;
}

export function FilterBar({ filters, onChange }: Props) {
  const exportEvents = useExport();
  const update = (key: keyof EventFilters, value: string) => {
    onChange({ ...filters, [key]: value || undefined });
  };

  const handleExport = async (format: "csv" | "json") => {
    try {
      await exportEvents(format, filters);
    } catch (e) {
      console.error("Export failed:", e);
    }
  };

  return (
    <div className="filter-bar" role="search" aria-label="Filter events">
      <div className="filter-fields">
        <label className="filter-field">
          <span className="filter-label">From</span>
          <input
            type="date"
            value={filters.date_from || ""}
            onChange={(e) => update("date_from", e.target.value)}
            aria-label="Start date"
          />
        </label>
        <label className="filter-field">
          <span className="filter-label">To</span>
          <input
            type="date"
            value={filters.date_to || ""}
            onChange={(e) => update("date_to", e.target.value)}
            aria-label="End date"
          />
        </label>
        <label className="filter-field">
          <span className="filter-label">Intent</span>
          <select
            value={filters.intent_type || ""}
            onChange={(e) => update("intent_type", e.target.value)}
            aria-label="Intent type"
          >
            <option value="">All</option>
            <option value="financial_compensation">Financial</option>
            <option value="refund_processing">Refund</option>
            <option value="policy_explanation">Policy</option>
            <option value="troubleshooting">Troubleshooting</option>
            <option value="general">General</option>
          </select>
        </label>
        <label className="filter-field">
          <span className="filter-label">Action</span>
          <select
            value={filters.action || ""}
            onChange={(e) => update("action", e.target.value)}
            aria-label="Action type"
          >
            <option value="">All</option>
            <option value="allow">Allow</option>
            <option value="correct">Correct</option>
            <option value="escalate">Escalate</option>
          </select>
        </label>
        <label className="filter-field">
          <span className="filter-label">Severity</span>
          <select
            value={filters.severity || ""}
            onChange={(e) => update("severity", e.target.value)}
            aria-label="Severity"
          >
            <option value="">All</option>
            <option value="low">Low</option>
            <option value="medium">Medium</option>
            <option value="high">High</option>
            <option value="critical">Critical</option>
          </select>
        </label>
      </div>
      <div className="filter-actions">
        <button
          className="btn btn-export"
          onClick={() => handleExport("csv")}
          aria-label="Export as CSV"
        >
          <Download size={14} aria-hidden="true" /> CSV
        </button>
        <button
          className="btn btn-export"
          onClick={() => handleExport("json")}
          aria-label="Export as JSON"
        >
          <Download size={14} aria-hidden="true" /> JSON
        </button>
      </div>
    </div>
  );
}
