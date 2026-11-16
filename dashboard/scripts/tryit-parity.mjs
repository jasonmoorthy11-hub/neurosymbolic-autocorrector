// Runs the assembled try-it assets (public/tryit/) under Node and compares every
// result with what CPython gave for the same replies.
//
//   node scripts/build-tryit.mjs
//   python3 ../scripts/tryit_expected.py > /tmp/expected.json
//   node scripts/tryit-parity.mjs /tmp/expected.json
//
// The expected file is a JSON list of {id, text, context, record}.
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const dashboard = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const assets = path.join(dashboard, "public", "tryit");
const expectedPath = process.argv[2];
if (!expectedPath) {
  console.error("usage: node scripts/tryit-parity.mjs <expected.json>");
  process.exit(2);
}
const expected = JSON.parse(fs.readFileSync(expectedPath, "utf8"));

const { loadPyodide } = await import(pathToFileURL(path.join(assets, "pyodide.mjs")).href);
const pyodide = await loadPyodide({ indexURL: assets + path.sep });
await pyodide.loadPackage(["pydantic", "cachetools"], { messageCallback: () => {} });
const core = JSON.parse(fs.readFileSync(path.join(assets, "core.json"), "utf8"));
for (const [file, source] of Object.entries(core.files)) {
  const target = `/core/${file}`;
  pyodide.FS.mkdirTree(target.slice(0, target.lastIndexOf("/")));
  pyodide.FS.writeFile(target, source);
}
pyodide.runPython("import sys; sys.path.insert(0, '/core'); import tryit_boot");
const check = pyodide.globals.get("tryit_boot").check;

// Key order is not part of the result
const canonical = (value) =>
  JSON.stringify(value, (_, v) =>
    v && typeof v === "object" && !Array.isArray(v)
      ? Object.fromEntries(Object.entries(v).sort(([a], [b]) => (a < b ? -1 : 1)))
      : v,
  );

const different = [];
for (const row of expected) {
  const got = JSON.parse(await check(row.text, JSON.stringify(row.context)));
  if (canonical(got) !== canonical(row.record)) different.push(row.id);
}
console.log(`tryit parity: ${expected.length - different.length} of ${expected.length} identical`);
if (different.length || expected.length === 0) {
  console.error(`different: ${different.slice(0, 20).join(", ")}`);
  process.exit(1);
}
