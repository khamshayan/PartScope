import { describe, expect, it, vi } from 'vitest';
import { z } from 'zod';

import { ApiError, badRequest, errorHandler, fromZod, serviceUnavailable } from '../src/errors.js';

const mockRes = () => {
  const res = {};
  res.status = vi.fn(() => res);
  res.json = vi.fn(() => res);
  return res;
};

describe('structured errors', () => {
  it('serialises to { error: { code, message } }', () => {
    expect(badRequest('nope').toJSON()).toEqual({
      error: { code: 'BAD_REQUEST', message: 'nope' },
    });
  });

  it('includes details when given', () => {
    const payload = serviceUnavailable('down', { hint: 'start it' }).toJSON();
    expect(payload.error.code).toBe('ML_SERVICE_UNAVAILABLE');
    expect(payload.error.details).toEqual({ hint: 'start it' });
  });

  it('flattens zod issues into readable field problems', () => {
    const schema = z.object({ items: z.array(z.string()).min(1) });
    const result = schema.safeParse({ items: [] });
    const error = fromZod(result.error);

    expect(error.status).toBe(400);
    expect(error.details[0].path).toBe('items');
  });

  it('never leaks a stack trace for an unexpected throw', () => {
    const res = mockRes();
    const boom = new Error('connection string is postgres://user:hunter2@host');
    boom.stack = 'Error: secret\n  at somewhere';

    // The handler logs server-side on purpose; silence it so a passing suite
    // does not print a scary-looking stack to stderr.
    const logged = vi.spyOn(console, 'error').mockImplementation(() => {});
    errorHandler(boom, {}, res, () => {});
    expect(logged).toHaveBeenCalled();
    logged.mockRestore();

    expect(res.status).toHaveBeenCalledWith(500);
    const body = JSON.stringify(res.json.mock.calls[0][0]);
    expect(body).not.toContain('hunter2');
    expect(body).not.toContain('at somewhere');
    expect(res.json.mock.calls[0][0]).toEqual({
      error: { code: 'INTERNAL_ERROR', message: 'An unexpected error occurred' },
    });
  });

  it('maps an oversized upload to 413', () => {
    const res = mockRes();
    errorHandler({ code: 'LIMIT_FILE_SIZE' }, {}, res, () => {});
    expect(res.status).toHaveBeenCalledWith(413);
  });

  it('passes an ApiError through with its own status', () => {
    const res = mockRes();
    errorHandler(new ApiError(418, 'TEAPOT', 'short and stout'), {}, res, () => {});
    expect(res.status).toHaveBeenCalledWith(418);
  });
});
