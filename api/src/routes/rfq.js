import { Router } from 'express';
import multer from 'multer';
import { z } from 'zod';

import { config } from '../config.js';
import { badRequest, fromZod, notFound } from '../errors.js';
import { findPartsByMpn } from '../db/mongo.js';
import { analyzeItems, parseFile, parseText } from '../services/mlClient.js';
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

const SPREADSHEET_NAME = /\.(xlsx|xlsm|xltx|xltm)$/i;

/**
 * Three ways in, one shape out.
 *
 * An explicit items array is taken as given. Everything else -- pasted text or
 * an uploaded file -- goes to the Python parser, which owns the question of
 * what a part number looks like. Doing that here in JavaScript as well is how
 * the two ends of the pipeline start disagreeing.
 */
async function resolveItems(input, file) {
  if (input.items) {
    return { items: input.items, skipped: [], diagnostics: { parser: 'client-supplied' } };
  }

  if (file) {
    const parsed = await parseFile(file.buffer, file.originalname, MAX_ITEMS);
    return { items: parsed.items, skipped: parsed.skipped, diagnostics: parsed.diagnostics };
  }

  const parsed = await parseText(input.raw_text, MAX_ITEMS);
  return { items: parsed.items, skipped: parsed.skipped, diagnostics: parsed.diagnostics };
}

/** What gets stored as raw_input when the upload was binary. */
function rawInputFor(input, file, diagnostics) {
  if (input.raw_text) return input.raw_text;
  if (file && SPREADSHEET_NAME.test(file.originalname)) {
    // A spreadsheet's bytes are not readable text, so record what was read
    // from it instead. An empty raw_input on a saved RFQ is worse than a
    // summary -- it makes the row look like a failed save.
    return [
      `[spreadsheet upload] ${file.originalname}`,
      diagnostics?.sheet ? `sheet: ${diagnostics.sheet}` : null,
      diagnostics?.part_column ? `part column: ${diagnostics.part_column}` : null,
      diagnostics?.quantity_column ? `quantity column: ${diagnostics.quantity_column}` : null,
    ].filter(Boolean).join('\n');
  }
  return file ? file.buffer.toString('utf8').slice(0, 200_000) : JSON.stringify(input.items);
}

rfqRouter.post('/', upload.single('file'), async (req, res, next) => {
  try {
    const body = req.body ?? {};
    const file = req.file;

    let sourceType = body.source_type;
    if (file && !sourceType) {
      sourceType = SPREADSHEET_NAME.test(file.originalname) ? 'spreadsheet' : 'email';
    }

    const parsedBody = createSchema.safeParse({
      ...body,
      // A file upload satisfies the "provide something" rule on its own.
      ...(file ? { raw_text: body.raw_text ?? undefined, items: undefined } : {}),
      ...(sourceType ? { source_type: sourceType } : {}),
      ...(file && !body.source_name ? { source_name: file.originalname } : {}),
    });

    if (!file && !parsedBody.success) throw fromZod(parsedBody.error);

    const input = parsedBody.success
      ? parsedBody.data
      : { source_type: sourceType ?? 'email', source_name: file?.originalname ?? null };

    const { items, skipped, diagnostics } = await resolveItems(input, file);

    if (!items.length) {
      throw badRequest('No part numbers could be read from that input', {
        skipped_lines: skipped.slice(0, 10),
        parser: diagnostics,
      });
    }

    const analysis = await analyzeItems(
      items.slice(0, MAX_ITEMS).map((item) => ({
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
      rawInput: rawInputFor(input, file, diagnostics),
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
      parser: diagnostics,
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
