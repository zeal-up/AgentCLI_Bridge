// Build-time constants injected by vite.config.ts (define). Declared here so
// TS knows about them. APP_VERSION is bumped manually per release; BUILD_TIME
// is the ISO timestamp of the build. Shown in the UI so you can tell whether a
// client (e.g. desktop Feishu) is serving a stale cached bundle.
declare const __APP_VERSION__: string;
declare const __BUILD_TIME__: string;

export const APP_VERSION: string = typeof __APP_VERSION__ !== 'undefined' ? __APP_VERSION__ : 'dev';
export const BUILD_TIME: string = typeof __BUILD_TIME__ !== 'undefined' ? __BUILD_TIME__ : '';

/** Short label for the UI, e.g. "v0.4.0 · 2026-07-02 17:00". */
export function versionLabel(): string {
  const t = BUILD_TIME ? BUILD_TIME.slice(0, 16).replace('T', ' ') : '';
  return `v${APP_VERSION}${t ? ` · ${t}` : ''}`;
}
