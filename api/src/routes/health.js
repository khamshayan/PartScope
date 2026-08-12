import { Router } from 'express';

import { pingMongo } from '../db/mongo.js';
import { pingPostgres } from '../db/postgres.js';
import { mlHealth } from '../services/mlClient.js';

export const healthRouter = Router();

/**
 * Reports each dependency separately.
 *
 * A single "ok: false" tells whoever is debugging nothing. Which of the three
 * is down is the entire question, so answer it -- and return 503 when any of
 * them is, so an uptime check does not read a degraded service as healthy.
 */
healthRouter.get('/', async (_req, res) => {
  const [postgres, mongo, ml] = await Promise.all([
    pingPostgres().then(() => ({ ok: true })).catch((e) => ({ ok: false, error: e.message })),
    pingMongo().then(() => ({ ok: true })).catch((e) => ({ ok: false, error: e.message })),
    mlHealth(),
  ]);

  const healthy = postgres.ok && mongo.ok && ml.reachable && ml.ready !== false;
  res.status(healthy ? 200 : 503).json({
    status: healthy ? 'ok' : 'degraded',
    postgres,
    mongo,
    ml_service: ml,
    data: 'synthetic - see docs/data-sources.md',
  });
});
