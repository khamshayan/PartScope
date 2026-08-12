import { Router } from 'express';

import { notFound } from '../errors.js';
import { findPartByMpn } from '../db/mongo.js';
import { fetchPartDetail } from '../services/mlClient.js';

export const partRouter = Router();

/**
 * Part detail: pricing and history from the ML service, catalog document from
 * Mongo.
 *
 * MPNs contain slashes -- D38999/24JA103SN, MCP645-I/SN, 23A1024-I/P -- so the
 * route has to accept the rest of the path as one parameter. `/api/part/:mpn`
 * alone would 404 on a third of the catalog.
 */
async function handler(req, res, next) {
  try {
    const mpn = decodeURIComponent(req.params.mpn ?? req.params[0] ?? '').trim();
    if (!mpn) throw notFound('No part number given');

    const [detail, catalog] = await Promise.all([
      fetchPartDetail(mpn),
      findPartByMpn(mpn),
    ]);

    if (!detail && !catalog) {
      throw notFound(`No catalog part matches ${mpn}`);
    }

    res.json({
      mpn,
      ...(detail ?? {}),
      catalog: catalog ?? null,
    });
  } catch (error) {
    next(error);
  }
}

partRouter.get('/:mpn', handler);
// Anything with a slash in it lands here instead.
partRouter.get('/:mpn/*', handler);
