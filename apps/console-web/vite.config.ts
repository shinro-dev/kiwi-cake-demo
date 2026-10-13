// Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
import react from '@vitejs/plugin-react';
import { defineConfig } from 'vitest/config';

// The dev server proxies the console API to a runtime daemon on the SAME machine. This is a loopback address: the
// console never reaches a network host, and a build serves the api from whatever origin it is hosted at.
const API_TARGET = 'http://127.0.0.1:8730';

export default defineConfig({
  plugins: [react()],
  // assetsInlineLimit 0: NOTHING is inlined as a data: URI. The API serves this build under a Content-Security-Policy
  // whose font-src is 'self' (apps/console-api/console_api/web.py), and vite's default limit inlined one small woff2,
  // which Chrome then refused with a csp violation on every load of the served page. Emitting every asset as a file
  // keeps the policy strict instead of widening it to data:.
  build: { outDir: 'build', emptyOutDir: true, sourcemap: true, assetsInlineLimit: 0 },
  server: {
    proxy: {
      '/api': { target: API_TARGET, changeOrigin: false },
    },
  },
  test: {
    include: ['src/**/*.test.ts'],
    environment: 'node',
    // A stylesheet imported with `?raw` is read as its text, so a test can check what it transforms; every other css
    // import stays empty in tests, as before.
    css: { include: [/\.css\?raw$/] },
  },
});
