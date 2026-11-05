import { ExternalLink } from "lucide-react";
import { useReplayContext } from "../replay/context";
import { laneBanner } from "../replay/lane";
import { REPO_URL } from "../replay/mode";

export function ReplayBanner() {
  const { lane } = useReplayContext();
  return (
    <div className="replay-banner" role="note">
      <span>{laneBanner(lane)}</span>
      <a href={REPO_URL} target="_blank" rel="noopener noreferrer">
        View source on GitHub <ExternalLink size={14} aria-hidden="true" />
        <span className="sr-only"> (opens in a new tab)</span>
      </a>
    </div>
  );
}
