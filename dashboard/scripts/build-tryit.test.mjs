import { createHash } from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { CORE, wheel } from "./build-tryit.mjs";

const good = Buffer.from("the real wheel");
const entry = { file_name: "pkg-1.0-py3-none-any.whl", sha256: createHash("sha256").update(good).digest("hex") };
const serving = (bytes) => async () => ({ ok: true, arrayBuffer: async () => bytes });

let cache;
beforeEach(() => {
  cache = fs.mkdtempSync(path.join(os.tmpdir(), "tryit-cache-"));
});
afterEach(() => fs.rmSync(cache, { recursive: true, force: true }));

describe("wheel", () => {
  it("keeps a download whose sha256 is the lockfile's", async () => {
    expect(await wheel(entry, "1", { fetchImpl: serving(good), cache })).toEqual(good);
    expect(fs.readFileSync(path.join(cache, entry.file_name))).toEqual(good);
  });

  it("refuses a download with other bytes and caches nothing", async () => {
    const fetchImpl = serving(Buffer.from("something else"));
    await expect(wheel(entry, "1", { fetchImpl, cache })).rejects.toThrow("sha256 differs");
    expect(fs.existsSync(path.join(cache, entry.file_name))).toBe(false);
  });

  it("does not trust a cached file with other bytes", async () => {
    fs.writeFileSync(path.join(cache, entry.file_name), "planted");
    expect(await wheel(entry, "1", { fetchImpl: serving(good), cache })).toEqual(good);
  });

  it("refuses an entry with no hash or with a path for a name", async () => {
    const never = async () => {
      throw new Error("must not fetch");
    };
    await expect(wheel({ ...entry, sha256: undefined }, "1", { fetchImpl: never, cache })).rejects.toThrow("no sha256");
    await expect(wheel({ ...entry, file_name: "../x.whl" }, "1", { fetchImpl: never, cache })).rejects.toThrow(
      "not a file name",
    );
  });
});

describe("CORE", () => {
  it("lists package sources only", () => {
    expect(CORE.length).toBeGreaterThan(0);
    for (const file of CORE) expect(file).toMatch(/^autocorrector\/[a-z_/]+\.py$/);
    expect(CORE.some((file) => file.includes("heldout") || file.includes("evaluation"))).toBe(false);
  });
});
