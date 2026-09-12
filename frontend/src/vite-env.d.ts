/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Base URL of the backend API; default "/api" (same-origin reverse proxy). */
  readonly VITE_API_BASE?: string;
  /** Optional bearer token attached as Authorization header to every request. */
  readonly VITE_API_TOKEN?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
