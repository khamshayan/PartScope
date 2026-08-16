import cors from 'cors';
import express from 'express';

import { errorHandler, notFoundHandler } from './errors.js';
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
        'GET  /api/health',
        'POST /api/rfq          { raw_text } | { items } | multipart file',
        'GET  /api/rfq          ?limit&offset',
        'GET  /api/rfq/:id',
        'GET  /api/part/:mpn',
      ],
    });
  });

  app.use('/api/health', healthRouter);
  app.use('/api/rfq', rfqRouter);
  app.use('/api/part', partRouter);

  app.use(notFoundHandler);
  app.use(errorHandler);

  return app;
}
