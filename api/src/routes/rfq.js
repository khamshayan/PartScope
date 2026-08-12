import { Router } from 'express';
import multer from 'multer';
import { z } from 'zod';

import { config } from '../config.js';
import { badRequest, fromZod, notFound } from '../errors.js';
import { findPartsByMpn } from '../db/mongo.js';
import { analyzeItems } from '../services/mlClient.js';
import { parseRfqText } from '../services/inputParser.js';
import { getRfq, listRfqs, saveRfq } from '../services/rfqRepository.js';

export const rfqRouter = Router();

const upload = multer({
  storage: multer.memoryStorage(),
  limits: { fileSize: config.upload.maxBytes, files: 1 },
});

const MAX_ITEMS = 200;

const createSchema = z
  .object({
    raw_text: z.string().min(1).max(200_000).optional(),
    items: z
      .array(
        z.object({
          input_string: z.string().min(1).max(200),
          quantity: z.coerce.number().int().positive().nullish(),
        }),
      )
      .min(1)
      .max(MAX_ITEMS)
      .optional(),
    source_type: z.enum(['text', 'email', 'spreadsheet']).default('text'),
    source_name: z.string().max(255).nullish(),
    model: z.enum(['naive', 'sarima', 'gbm']).nullish(),
  })
  .refine((body) => Boolean(body.raw_text || body.items), {
    message: 'Provide raw_text, an items array, or upload a file',
  });

const listSchema = z.object({
  limit: z.coerce.number().int().min(1).max(100).default(20),
  offset: z.coerce.number().int().min(0).default(0),
});

const idSchema = z.coerce.number().int().positive();

/** Spreadsheets are binary; Phase 5 adds real parsing in the Python service. */
const SPREADSHEET_TYPES = /\.(xlsx|xlsm|xls)$/i;

function decodeUpload(file) {
  if (SPREADSHEET_TYPES.test(file.originalname)) {
    throw badRequest(
      'Spreadsheet parsing is not available yet (Phase 5). Paste the part numbers as text for now.',
      { filename: file.originalname },
    );
  }
  const text = file.buffer.toString('utf8');
  // A binary file decoded as UTF-8 is mostly replacement characters; catching
  // it here gives a usable message instead of a table of garbage rows.
  const replacementRatio =
    (text.match(/�/g)?.length ?? 0) / Math.max(text.length, 1);
  if (replacementRatio > 0.1) {
    throw badRequest('Uploaded file does not look like text', {
      filename: file.originalname,
    });
  }
  return text;
}

rfqRouter.post('/', upload.single('file'), async (req, res, next) => {
  try {
    const body = req.body ?? {};
    let rawText = body.raw_text;
    let sourceType = body.source_type;
    let sourceName = body.source_name;

    if (req.file) {
      rawText = decodeUpload(req.file);
      sourceName = sourceName ?? req.file.originalname;
      sourceType = sourceType ?? 'email';
    }

    const parsed = createSchema.safeParse({
      ...body,
      ...(rawText !== undefined ? { raw_text: rawText } : {}),
      ...(sourceName !== undefined && sourceName !== null ? { source_name: sourceName } : {}),
      ...(sourceType !== undefined ? { source_type: sourceType } : {}),
    });
    if (!parsed.success) throw fromZod(parsed.error);

    const input = parsed.data;
    const { items, skipped } = input.items
      ? { items: input.items, skipped: [] }
      : parseRfqText(input.raw_text, { maxItems: MAX_ITEMS });

    if (!items.length) {
      throw badRequest('No part numbers could be read from that input', {
        skipped_lines: skipped.slice(0, 10),
      });
    }

    const analysis = await analyzeItems(
      items.map((item) => ({
        input_string: item.input_string,
        quantity: item.quantity ?? null,
      })),
      input.model ?? undefined,
    );

    // Enrich with catalog detail straight from Mongo. The ML service returns
    // what it needs for matching and pricing; the full datasheet specs and
    // stock position live in the document store.
    const mpns = analysis.items.map((item) => item.matched_mpn).filter(Boolean);
    const catalog = await findPartsByMpn(mpns);
    const enriched = analysis.items.map((item) => ({
      ...item,
      catalog: item.matched_mpn ? (catalog.get(item.matched_mpn) ?? null) : null,
    }));

    const saved = await saveRfq({
      rawInput: input.raw_text ?? JSON.stringify(input.items),
      sourceType: input.source_type,
      sourceName: input.source_name ?? null,
      items: enriched,
    });

    res.status(201).json({
      rfq_id: saved.id,
      created_at: saved.createdAt,
      source_type: input.source_type,
      source_name: input.source_name ?? null,
      count: enriched.length,
      skipped_lines: skipped,
      model: analysis.model,
      elapsed_ms: analysis.elapsed_ms,
      items: enriched,
    });
  } catch (error) {
    next(error);
  }
});

rfqRouter.get('/', async (req, res, next) => {
  try {
    const parsed = listSchema.safeParse(req.query);
    if (!parsed.success) throw fromZod(parsed.error);
    res.json(await listRfqs(parsed.data));
  } catch (error) {
    next(error);
  }
});

rfqRouter.get('/:id', async (req, res, next) => {
  try {
    const parsed = idSchema.safeParse(req.params.id);
    if (!parsed.success) throw badRequest('RFQ id must be a positive integer');

    const rfq = await getRfq(parsed.data);
    if (!rfq) throw notFound(`No RFQ with id ${parsed.data}`);
    res.json(rfq);
  } catch (error) {
    next(error);
  }
});
