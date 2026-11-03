/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** "1" builds the backend-free replay dashboard (scripts/record_replay.py data). */
  readonly VITE_REPLAY?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
