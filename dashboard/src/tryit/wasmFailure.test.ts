import { describe, expect, it } from "vitest";
import { wasmFailure, type WasmApi } from "./wasmFailure";

const api = (outcome: () => Promise<unknown>): WasmApi => ({
  instantiate: () => outcome(),
  instantiateStreaming: () => outcome(),
});

describe("wasmFailure", () => {
  it.each(["instantiate", "instantiateStreaming"] as const)("rejects when %s does, and the caller still sees it", async (name) => {
    const wasm = api(() => Promise.reject(new TypeError("Failed to fetch")));
    const failed = wasmFailure(wasm);
    await expect(wasm[name]("module", {})).rejects.toThrow("Failed to fetch");
    await expect(failed).rejects.toThrow("Failed to fetch");
  });

  it("stays pending, and passes the result and arguments through, when the module loads", async () => {
    const seen: unknown[][] = [];
    const wasm = {
      instantiate: async (...args: unknown[]) => (seen.push(args), "instance"),
      instantiateStreaming: async (...args: unknown[]) => (seen.push(args), "instance"),
    };
    const failed = wasmFailure(wasm);
    expect(await wasm.instantiateStreaming("response", "imports")).toBe("instance");
    expect(seen).toEqual([["response", "imports"]]);
    const settled = await Promise.race([failed.then(() => "settled", () => "settled"), Promise.resolve("pending")]);
    expect(settled).toBe("pending");
  });
});
