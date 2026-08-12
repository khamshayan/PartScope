import react from '@vitejs/plugin-react';
import { defineConfig, loadEnv } from 'vite';

export default defineConfig(({ mode }) => {
  // The API base is read from the repo-root .env so there is one place that
  // knows the port, shared with the Node and Python services.
  const env = loadEnv(mode, process.cwd() + '/..', '');
  const apiTarget = env.VITE_API_BASE_URL || 'http://localhost:3000';

  return {
    plugins: [react()],
    server: {
      port: 5173,
      // Proxying keeps the browser on one origin, so there is no CORS
      // preflight on every analyse and no API URL baked into the bundle.
      proxy: {
        '/api': { target: apiTarget, changeOrigin: true },
      },
    },
    build: { outDir: 'dist', sourcemap: true },
  };
});
