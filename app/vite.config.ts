import path from 'path';
import { defineConfig } from '@lark-apaas/fullstack-vite-preset';

// Stamped at build time. Bump APP_VERSION on releases; BUILD_TIME auto-updates.
const APP_VERSION = '0.4.9';
const BUILD_TIME = new Date().toISOString();

export default defineConfig({
  resolve: {
    alias: {
      '@': path.resolve(__dirname, 'client/src'),
    },
  },
  define: {
    __APP_VERSION__: JSON.stringify(APP_VERSION),
    __BUILD_TIME__: JSON.stringify(BUILD_TIME),
  },
});
