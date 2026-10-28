/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Absolute base for the GroundTruth server. Empty means same-origin (dev proxy). */
  readonly VITE_GROUNDTRUTH_SERVER?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
