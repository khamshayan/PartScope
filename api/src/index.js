import { createApp } from './app.js';
import { config } from './config.js';
import { closeMongo } from './db/mongo.js';
import { closePostgres } from './db/postgres.js';

const app = createApp();

const server = app.listen(config.port, () => {
  console.log(`[api] listening on http://localhost:${config.port}`);
  console.log(`[api] ml service at ${config.mlService.url}`);
  console.log('[api] all data is synthetic - see docs/data-sources.md');
});

async function shutdown(signal) {
  console.log(`\n[api] ${signal} received, shutting down`);
  server.close();
  await Promise.allSettled([closePostgres(), closeMongo()]);
  process.exit(0);
}

for (const signal of ['SIGINT', 'SIGTERM']) {
  process.on(signal, () => shutdown(signal));
}
