/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_BACKEND_BASE_URL: string;
  readonly VITE_ECHO_BASE_URL: string;
  readonly VITE_STATIC_WEBSITE_ONLY: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
