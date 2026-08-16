import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import dotenv from 'dotenv';

const here = dirname(fileURLToPath(import.meta.url));
export const REPO_ROOT = resolve(here, '..', '..');

// A missing .env is fine: every default below matches what docker-compose
// brings up, so a clean clone runs with no configuration at all.
dotenv.config({ path: resolve(REPO_ROOT, '.env') });

const int = (value, fallback) => {
  const parsed = Number.parseInt(value ?? '', 10);
  return Number.isFinite(parsed) ? parsed : fallback;
};

export const config = {
  port: int(process.env.API_PORT, 3000),

  postgres: {
    host: process.env.POSTGRES_HOST ?? 'localhost',
    port: int(process.env.POSTGRES_PORT, 5433),
    database: process.env.POSTGRES_DB ?? 'partscope',
    user: process.env.POSTGRES_USER ?? 'partscope',
    password: process.env.POSTGRES_PASSWORD ?? 'partscope',
  },

  mongo: {
    uri: process.env.MONGO_URI ?? 'mongodb://localhost:27018',
    database: process.env.MONGO_DB ?? 'partscope',
  },

  mlService: {
    url: process.env.ML_SERVICE_URL ?? 'http://localhost:8000',
    // Generous: a cold SARIMA order search on an unseen part legitimately
    // takes seconds. The client gets a structured 503 rather than a hang.
    timeoutMs: int(process.env.ML_SERVICE_TIMEOUT_MS, 30000),
  },

  upload: {
    maxBytes: int(process.env.UPLOAD_MAX_BYTES, 5 * 1024 * 1024),
  },
};
