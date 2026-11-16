// Runs the rule core in Pyodide, off the main thread. Everything it fetches is
// same-origin, under <base>/tryit/ (scripts/build-tryit.mjs); nothing typed into
// the panel leaves this worker.
import { LOAD_STEPS, type TryItMessage, type TryItRequest } from "./protocol";

type Check = (text: string, context: string) => Promise<string>;

interface Pyodide {
  loadPackage(names: string[], options?: { messageCallback?: (m: string) => void }): Promise<unknown>;
  runPython(code: string): unknown;
  loadedPackages: Record<string, string>;
  FS: { mkdirTree(path: string): void; writeFile(path: string, data: string): void };
  globals: { get(name: string): { check: Check } };
}

const scope = self as unknown as {
  postMessage(message: TryItMessage): void;
  onmessage: ((event: MessageEvent<TryItRequest>) => void) | null;
  location: { origin: string };
};
const base = new URL(`${import.meta.env.BASE_URL}tryit/`, scope.location.origin).href;

const PACKAGES = ["pydantic", "cachetools"] as const;

let check: Check | null = null;

const progress = (step: number) =>
  scope.postMessage({ type: "progress", step, label: LOAD_STEPS[step - 1] });

async function load(): Promise<void> {
  progress(1);
  const runtime = (await import(/* @vite-ignore */ `${base}pyodide.mjs`)) as {
    loadPyodide(options: { indexURL: string }): Promise<Pyodide>;
  };
  const pyodide = await runtime.loadPyodide({ indexURL: base });
  progress(2);
  await pyodide.loadPackage([...PACKAGES], { messageCallback: () => {} });
  // loadPackage resolves even when a wheel could not be fetched or failed its hash
  const missing = PACKAGES.filter((name) => !(name in pyodide.loadedPackages));
  if (missing.length) throw new Error(`not loaded: ${missing.join(", ")}`);
  progress(3);
  const response = await fetch(`${base}core.json`);
  if (!response.ok) throw new Error(`core.json: HTTP ${response.status}`);
  const core = (await response.json()) as { files: Record<string, string> };
  for (const [file, source] of Object.entries(core.files)) {
    const target = `/core/${file}`;
    pyodide.FS.mkdirTree(target.slice(0, target.lastIndexOf("/")));
    pyodide.FS.writeFile(target, source);
  }
  pyodide.runPython("import sys; sys.path.insert(0, '/core'); import tryit_boot");
  check = pyodide.globals.get("tryit_boot").check;
  scope.postMessage({ type: "ready" });
}

scope.onmessage = (event) => {
  const request = event.data;
  if (request.type === "load") {
    load().catch((error: unknown) => {
      // The reason (a URL, a Python traceback) is for the console, not the page
      console.error("try-it: load failed", error);
      scope.postMessage({ type: "error", message: "The checker could not be loaded." });
    });
    return;
  }
  if (!check) {
    scope.postMessage({ type: "error", message: "The checker is not loaded yet." });
    return;
  }
  const started = performance.now();
  check(request.text, JSON.stringify(request.context))
    .then((json) =>
      scope.postMessage({
        type: "result",
        id: request.id,
        result: JSON.parse(json),
        ms: performance.now() - started,
      }),
    )
    .catch((error: unknown) => {
      console.error("try-it: check failed", error);
      scope.postMessage({ type: "error", message: "The check failed on this reply." });
    });
};
