import cors from 'cors';
import express from 'express';

import { requireAuth } from './auth.js';
import { errorHandler, notFoundHandler } from './errors.js';
import { authRouter } from './routes/auth.js';
import { healthRouter } from './routes/health.js';
import { partRouter } from './routes/part.js';
import { rfqRouter } from './routes/rfq.js';

/** Built as a factory so tests can mount the app without binding a port. */
export function createApp() {
  const app = express();

  app.use(cors());
  app.use(express.json({ limit: '1mb' }));
  app.use(express.urlencoded({ extended: true, limit: '1mb' }));

  app.get('/', (_req, res) => {
    res.json({
      service: 'PartScope API',
      version: '0.3.0',
      data: 'synthetic - see docs/data-sources.md',
      endpoints: [
        'POST /api/login        { username, password }',
        'POST /api/logout',
        'GET  /api/me',
        'GET  /api/health',
        'POST /api/rfq          { raw_text } | { items } | multipart file',
        'GET  /api/rfq          ?limit&offset',
        'GET  /api/rfq/:id',
        'GET  /api/part/:mpn',
      ],
    });
  });

  // Public: you cannot require a session in order to establish one. This is
  // mounted at /api rather than at three paths so the three sibling endpoints
  // stay in one file; anything it does not define falls through to the gated
  // routers below.
  app.use('/api', authRouter);

  // Everything else is behind the gate, health included -- its response names
  // which datastore is down and why, which is a diagnostic for whoever runs
  // this, not for anyone who can reach the port. The `/` banner above stays
  // public as the liveness probe.
  app.use('/api/health', requireAuth, healthRouter);
  app.use('/api/rfq', requireAuth, rfqRouter);
  app.use('/api/part', requireAuth, partRouter);

  app.use(notFoundHandler);
  app.use(errorHandler);

  return app;
}
