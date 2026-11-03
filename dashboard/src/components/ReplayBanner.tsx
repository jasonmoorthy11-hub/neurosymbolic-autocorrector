import { ExternalLink } from "lucide-react";
import { REPO_URL } from "../replay/mode";

export function ReplayBanner() {
  return (
    <div className="replay-banner" role="note">
      <span>Recorded runs of the offline pipeline — no backend, no API key</span>
      <a href={REPO_URL} target="_blank" rel="noopener noreferrer">
        View source on GitHub <ExternalLink size={14} aria-hidden="true" />
        <span className="sr-only"> (opens in a new tab)</span>
      </a>
    </div>
  );
}
