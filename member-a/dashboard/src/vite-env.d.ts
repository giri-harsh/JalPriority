/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_A_HARNESS_PLAN_SOURCE?: "solve" | "fixture";
  readonly VITE_A_DEV_TOKEN?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
