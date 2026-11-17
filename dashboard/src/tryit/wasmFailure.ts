type Instantiate = (...args: unknown[]) => Promise<unknown>;

/** The two entry points of `WebAssembly` that a runtime's main module goes through. */
export interface WasmApi {
  instantiate: Instantiate;
  instantiateStreaming: Instantiate;
}

/**
 * A promise that rejects when a WebAssembly module fails to download or compile.
 *
 * Pyodide logs that failure ("wasm instantiation failed!") and then leaves
 * `loadPyodide` pending for ever, so the load is raced against this. It wraps
 * the two functions on the object it is given, and callers still get the
 * rejection themselves.
 */
export function wasmFailure(wasm: WasmApi): Promise<never> {
  return new Promise((_, reject) => {
    for (const name of ["instantiate", "instantiateStreaming"] as const) {
      const real = wasm[name];
      wasm[name] = (...args) =>
        real.apply(wasm, args).catch((error: unknown) => {
          reject(error);
          throw error;
        });
    }
  });
}
