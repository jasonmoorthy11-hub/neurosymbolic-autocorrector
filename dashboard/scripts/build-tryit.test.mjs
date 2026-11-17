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

  const never = async () => {
    throw new Error("must not fetch");
  };

  it("returns a cached file with the right bytes without fetching", async () => {
    fs.writeFileSync(path.join(cache, entry.file_name), good);
    expect(await wheel(entry, "1", { fetchImpl: never, cache })).toEqual(good);
  });

  it("stops on a failed download and caches nothing", async () => {
    const fetchImpl = async () => ({ ok: false, status: 404 });
    await expect(wheel(entry, "1", { fetchImpl, cache })).rejects.toThrow("HTTP 404");
    expect(fs.readdirSync(cache)).toEqual([]);
  });

  it("refuses a name that is not a plain wheel file name", async () => {
    for (const file_name of ["", ".", "..", "x.whl?y", "x.whl#f", "a/x.whl", "x.tar.gz"]) {
      await expect(wheel({ ...entry, file_name }, "1", { fetchImpl: never, cache }), file_name).rejects.toThrow(
        "not a file name",
      );
    }
  });

  it("refuses an entry with no hash or with a path for a name", async () => {
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
