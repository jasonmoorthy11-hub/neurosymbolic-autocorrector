import { useState } from "react";
import { useWebSocket } from "./hooks/useWebSocket";
import { useMetrics, useQueue, useRules, useAnalyticsTrends, useAnalyticsSummary } from "./hooks/useApi";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { MetricsPanel } from "./components/MetricsPanel";
import { AgentFeed } from "./components/AgentFeed";
import { RulesPanel } from "./components/RulesPanel";
import { SupervisorQueue } from "./components/SupervisorQueue";
import { EventChart } from "./components/EventChart";
import { AnalyticsPanel } from "./components/AnalyticsPanel";
import { DemoControls } from "./components/DemoControls";
import { FilterBar } from "./components/FilterBar";
import type { EventFilters } from "./types/api";
import "./App.css";

function App() {
  const [filters, setFilters] = useState<EventFilters>({});
  const { events, connected, reconnecting } = useWebSocket();
  const { metrics, error: metricsError, loading: metricsLoading, refresh: refreshMetrics } = useMetrics();
  const { queue, error: queueError, loading: queueLoading, refresh: refreshQueue } = useQueue();
  const { rules, error: rulesError, loading: rulesLoading, refresh: refreshRules } = useRules();
  const { data: trends, error: trendsError, loading: trendsLoading, refresh: refreshTrends } = useAnalyticsTrends();
  const { data: summary, error: summaryError, loading: summaryLoading, refresh: refreshSummary } = useAnalyticsSummary();

  return (
    <ErrorBoundary>
      <div className="app">
        <a href="#main-content" className="skip-link">
          Skip to main content
        </a>

        {(!connected || reconnecting) && (
          <div className="connection-banner" role="alert">
            {reconnecting
              ? "Reconnecting to server..."
              : "Disconnected from server"}
          </div>
        )}

        <header className="app-header">
          <div className="header-left">
            <h1>Neurosymbolic Autocorrector</h1>
            <span className="header-subtitle">Real-Time Correction Dashboard</span>
          </div>
          <DemoControls connected={connected} />
        </header>

        <main id="main-content" className="app-main">
          <MetricsPanel
            metrics={metrics}
            error={metricsError}
            loading={metricsLoading}
            onRetry={refreshMetrics}
          />
          <EventChart metrics={metrics} />
          <AnalyticsPanel
            trends={trends}
            summary={summary}
            error={trendsError || summaryError}
            loading={trendsLoading || summaryLoading}
            onRetry={() => { refreshTrends(); refreshSummary(); }}
          />

          <FilterBar filters={filters} onChange={setFilters} />

          <div className="main-grid">
            <AgentFeed events={events} />
            <div className="side-panels">
              <SupervisorQueue
                queue={queue}
                error={queueError}
                loading={queueLoading}
                onRetry={refreshQueue}
                onDecision={refreshQueue}
              />
              <RulesPanel
                rules={rules}
                error={rulesError}
                loading={rulesLoading}
                onRetry={refreshRules}
              />
            </div>
          </div>
        </main>
      </div>
    </ErrorBoundary>
  );
}

export default App;
