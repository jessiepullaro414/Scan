/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** "1" runs the UI against the in-browser mock backend. */
  readonly VITE_MOCK?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
