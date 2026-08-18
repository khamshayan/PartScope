/// <reference types="vite/client" />

interface ImportMetaEnv {
  /**
   * Origin of the Express API, e.g. "https://partscope-api.onrender.com".
   *
   * Unset in local dev, where vite.config.ts proxies /api instead and the
   * client falls back to relative paths. Required for a static build whose
   * host is not also serving the API.
   *
   * Declared here so it is `string | undefined` rather than the `any` that
   * vite/client's index signature would otherwise give it -- the fallback in
   * api/client.ts depends on the undefined case being visible to the compiler.
   */
  readonly VITE_API_BASE_URL?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
