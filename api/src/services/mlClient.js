import axios from 'axios';

import { config } from '../config.js';
import { badRequest, serviceUnavailable } from '../errors.js';

const client = axios.create({
  baseURL: config.mlService.url,
  timeout: config.mlService.timeoutMs,
  headers: { 'Content-Type': 'application/json' },
});

/**
 * Translate a transport failure into a 503 the client can act on.
 *
 * The Python service being down is an operational condition, not a bug in the
 * request. It must not surface as a 500 with a stack trace: the caller needs to
 * know it should retry, and a human needs to know which process to restart.
 */
function asServiceError(error, action) {
  const detail = {
    ml_service_url: config.mlService.url,
    hint: 'Is the ML service running?  make dev  (or: uvicorn app.main:app --port 8000)',
  };

  if (error.code === 'ECONNREFUSED' || error.code === 'ENOTFOUND') {
    return serviceUnavailable(`Cannot reach the ML service to ${action}`, detail);
  }
  if (error.code === 'ECONNABORTED' || error.code === 'ETIMEDOUT') {
    return serviceUnavailable(
      `The ML service timed out while trying to ${action}`,
      { ...detail, timeout_ms: config.mlService.timeoutMs },
    );
  }

  const status = error.response?.status;
  if (status === 404) return null; // caller decides what a miss means
  if (status === 400) {
    // The upstream rejected the input itself -- an unreadable file, an empty
    // body. That is the caller's problem to fix, so it must not become a 500.
    return badRequest(
      String(error.response?.data?.detail ?? `Could not ${action}`),
    );
  }
  if (status && status >= 500) {
    return serviceUnavailable(`The ML service failed while trying to ${action}`, {
      ...detail,
      upstream_status: status,
      upstream_detail: error.response?.data?.detail,
    });
  }
  return null;
}

/**
 * Parsing lives in the Python tier, not here.
 *
 * There was a JavaScript parser in this file's neighbourhood through Phases 3
 * and 4. It is gone deliberately: two implementations of "what is a part
 * number" drift apart, and the drift shows up as a line item that the API reads
 * one way and the matcher another. The Python one has the token scorer the
 * spreadsheet column inference also depends on, so that is the one that stays.
 */
export async function parseText(rawText, maxItems) {
  try {
    const { data } = await client.post('/parse', {
      raw_text: rawText,
      ...(maxItems ? { max_items: maxItems } : {}),
    });
    return data;
  } catch (error) {
    const mapped = asServiceError(error, 'parse the RFQ text');
    if (mapped) throw mapped;
    throw error;
  }
}

export async function parseFile(buffer, filename, maxItems) {
  const form = new FormData();
  form.append('file', new Blob([buffer]), filename || 'upload');
  if (maxItems) form.append('max_items', String(maxItems));

  try {
    const { data } = await client.post('/parse/file', form, {
      headers: { 'Content-Type': undefined },
      maxBodyLength: Infinity,
    });
    return data;
  } catch (error) {
    const mapped = asServiceError(error, `parse ${filename}`);
    if (mapped) throw mapped;
    throw error;
  }
}

export async function analyzeItems(items, model) {
  try {
    const { data } = await client.post('/analyze', { items, model: model ?? null });
    return data;
  } catch (error) {
    const mapped = asServiceError(error, 'analyze the RFQ');
    if (mapped) throw mapped;
    throw error;
  }
}

export async function fetchPartDetail(mpn) {
  try {
    // MPNs contain slashes (D38999/24JA103SN), so each segment must be encoded
    // or the upstream router splits the part number into path segments.
    const { data } = await client.get(`/part/${encodeURIComponent(mpn)}`);
    return data;
  } catch (error) {
    if (error.response?.status === 404) return null;
    const mapped = asServiceError(error, `fetch detail for ${mpn}`);
    if (mapped) throw mapped;
    throw error;
  }
}

export async function mlHealth() {
  try {
    const { data } = await client.get('/health', { timeout: 3000 });
    return { reachable: true, ...data };
  } catch (error) {
    return { reachable: false, error: error.code ?? error.message };
  }
}
