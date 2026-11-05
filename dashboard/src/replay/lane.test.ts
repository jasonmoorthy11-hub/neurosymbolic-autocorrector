import { describe, expect, it } from "vitest";
import { laneBanner, laneFromSearch, laneLabel, laneSearch, latencyPrefix, replayFileFor } from "./lane";

describe("laneFromSearch", () => {
  it("defaults to the deterministic recording", () => {
    expect(laneFromSearch("")).toBe("deterministic");
    expect(laneFromSearch("?v=abc")).toBe("deterministic");
  });

  it("selects the LLM lane with ?lane=llm, wherever it sits", () => {
    expect(laneFromSearch("?lane=llm")).toBe("llm");
    expect(laneFromSearch("?v=abc&lane=llm")).toBe("llm");
  });

  it("ignores unknown lanes", () => {
    expect(laneFromSearch("?lane=bogus")).toBe("deterministic");
    expect(laneFromSearch("?lane=")).toBe("deterministic");
  });
});

describe("laneSearch", () => {
  it("adds and removes the lane parameter, keeping other parameters", () => {
    expect(laneSearch("", "llm")).toBe("?lane=llm");
    expect(laneSearch("?v=abc", "llm")).toBe("?v=abc&lane=llm");
    expect(laneSearch("?lane=llm", "deterministic")).toBe("");
    expect(laneSearch("?v=abc&lane=llm", "deterministic")).toBe("?v=abc");
  });
});

describe("replayFileFor", () => {
  it("maps each lane to its recording", () => {
    expect(replayFileFor("deterministic")).toBe("replay/replay.json");
    expect(replayFileFor("llm")).toBe("replay/replay_llm.json");
  });
});

describe("labels", () => {
  it("names the lanes", () => {
    expect(laneLabel("deterministic")).toBe("Deterministic");
    expect(laneLabel("llm")).toBe("LLM tier · Haiku 5.5");
  });

  it("keeps the deterministic banner text and describes the LLM recording", () => {
    expect(laneBanner("deterministic")).toBe(
      "Recorded runs of the offline pipeline — no backend, no API key",
    );
    expect(laneBanner("llm")).toBe(
      "One recorded run with the LLM correction tier (Claude Haiku 5.5) — no backend, no API key at view time",
    );
  });

  it("prefixes the latency line by the file's mode", () => {
    expect(latencyPrefix("offline-deterministic")).toBe("offline path");
    expect(latencyPrefix("llm-recorded")).toBe("LLM tier");
    expect(latencyPrefix("something-else")).toBe("recorded");
  });
});
