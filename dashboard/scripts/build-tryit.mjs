// Assembles what the "Try it" panel loads in the browser into public/tryit/:
// the Pyodide runtime (from the pinned npm package), the pydantic and cachetools
// wheels (downloaded once, each checked against the sha256 in Pyodide's lockfile),
// and the rule core of ../src/autocorrector as one JSON file.
//
//   node scripts/build-tryit.mjs
//
// public/tryit/ is not committed; the Pages and CI builds run this first.
import { createHash } from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const dashboard = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const pyodideDir = path.join(dashboard, "node_modules", "pyodide");
const cacheDir = path.join(dashboard, "node_modules", ".cache", "tryit");
const outDir = path.join(dashboard, "public", "tryit");
const srcDir = path.resolve(dashboard, "..", "src");

const RUNTIME = ["pyodide.mjs", "pyodide.asm.mjs", "pyodide.asm.wasm", "python_stdlib.zip"];
const PACKAGES = ["pydantic", "cachetools"];
// The import closure of orchestrator/runner.py. tests/test_runner_parity.py keeps
// the runner free of anything else; scripts/tryit-parity.mjs runs this exact set.
export const CORE = [
  "autocorrector/__init__.py",
  "autocorrector/models.py",
  "autocorrector/llm_response.py",
  "autocorrector/explainability/__init__.py",
  "autocorrector/explainability/engine.py",
  "autocorrector/guardrails/__init__.py",
  "autocorrector/guardrails/pii.py",
  "autocorrector/intent_classifier/__init__.py",
  "autocorrector/intent_classifier/classifier.py",
  "autocorrector/knowledge_graph/__init__.py",
  "autocorrector/knowledge_graph/claims.py",
  "autocorrector/knowledge_graph/defaults.py",
  "autocorrector/knowledge_graph/facts.py",
  "autocorrector/knowledge_graph/keywords.py",
  "autocorrector/knowledge_graph/rewriter.py",
  "autocorrector/knowledge_graph/rules.py",
  "autocorrector/knowledge_graph/store.py",
  "autocorrector/knowledge_graph/templates.py",
  "autocorrector/multilingual/__init__.py",
  "autocorrector/multilingual/detector.py",
  "autocorrector/orchestrator/__init__.py",
  "autocorrector/orchestrator/runner.py",
];

// Uncompressed. 15.4 MB when written (Pyodide 314.0.8); a jump past this is a
// decision, not a side effect of a version bump.
const BUDGET_BYTES = 17_000_000;

const normalise = (name) => name.toLowerCase().replace(/[-_.]+/g, "-");
const sha256 = (bytes) => createHash("sha256").update(bytes).digest("hex");

/** One wheel's bytes, from the cache or the CDN, only if they match the lockfile's sha256. */
export async function wheel(entry, version, { fetchImpl = fetch, cache = cacheDir } = {}) {
  if (!/^[0-9a-f]{64}$/.test(entry.sha256 ?? "")) throw new Error(`${entry.file_name}: no sha256 in the lockfile`);
  if (!/^[\w+-][\w.+-]*\.whl$/.test(entry.file_name ?? "")) throw new Error(`${entry.file_name}: not a file name`);
  const cached = path.join(cache, entry.file_name);
  if (fs.existsSync(cached)) {
    const kept = fs.readFileSync(cached);
    if (sha256(kept) === entry.sha256) return kept;
  }
  const url = `https://cdn.jsdelivr.net/pyodide/v${version}/full/${entry.file_name}`;
  const response = await fetchImpl(url);
  if (!response.ok) throw new Error(`${url}: HTTP ${response.status}`);
  const bytes = Buffer.from(await response.arrayBuffer());
  if (sha256(bytes) !== entry.sha256) {
    throw new Error(`${entry.file_name}: sha256 differs from pyodide-lock.json; not used`);
  }
  fs.mkdirSync(cache, { recursive: true });
  fs.writeFileSync(cached, bytes);
  return bytes;
}

export async function build() {
  const version = JSON.parse(fs.readFileSync(path.join(pyodideDir, "package.json"), "utf8")).version;
  const lock = JSON.parse(fs.readFileSync(path.join(pyodideDir, "pyodide-lock.json"), "utf8"));

  const needed = new Map();
  const visit = (name) => {
    const key = normalise(name);
    if (needed.has(key)) return;
    const entry = lock.packages[key];
    if (!entry) throw new Error(`${name} is not in pyodide-lock.json`);
    needed.set(key, entry);
    entry.depends.forEach(visit);
  };
  PACKAGES.forEach(visit);

  fs.rmSync(outDir, { recursive: true, force: true });
  fs.mkdirSync(outDir, { recursive: true });
  const files = [];
  const emit = (name, bytes) => {
    fs.writeFileSync(path.join(outDir, name), bytes);
    files.push({ name, bytes: bytes.length });
  };

  for (const name of RUNTIME) emit(name, fs.readFileSync(path.join(pyodideDir, name)));
  for (const entry of needed.values()) emit(entry.file_name, await wheel(entry, version));
  // Only the packages this page loads: the full lockfile lists a few hundred
  emit(
    "pyodide-lock.json",
    Buffer.from(JSON.stringify({ info: lock.info, packages: Object.fromEntries(needed) })),
  );

  const core = { "tryit_boot.py": fs.readFileSync(path.join(dashboard, "scripts", "tryit_boot.py"), "utf8") };
  for (const file of CORE) core[file] = fs.readFileSync(path.join(srcDir, file), "utf8");
  emit("core.json", Buffer.from(JSON.stringify({ files: core })));

  const total = files.reduce((sum, file) => sum + file.bytes, 0);
  if (total > BUDGET_BYTES) {
    throw new Error(`try-it assets are ${total} bytes, over the ${BUDGET_BYTES} budget`);
  }
  fs.writeFileSync(
    path.join(outDir, "manifest.json"),
    JSON.stringify({ pyodide: version, python: lock.info.python, total_bytes: total, files }, null, 1),
  );
  return { version, python: lock.info.python, total, files };
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const { version, python, total, files } = await build();
  console.log(`tryit: Pyodide ${version} (Python ${python}), ${files.length} files, ${total} bytes`);
}
