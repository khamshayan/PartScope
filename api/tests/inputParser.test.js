import { describe, expect, it } from 'vitest';

import { parseRfqText } from '../src/services/inputParser.js';

/**
 * The provisional RFQ text parser (Phase 5 replaces it with the real one).
 *
 * Two of these cases are regressions from bugs found running real input
 * through the stack, and both were silent: a corrupted part number and a
 * sentence in the results table both look like data until you read them.
 */

const parse = (text) => parseRfqText(text).items;

describe('parseRfqText', () => {
  it('reads one part per line', () => {
    expect(parse('STM32F130ZBT6\nMMSZ4744AT1G')).toEqual([
      { input_string: 'STM32F130ZBT6', quantity: null },
      { input_string: 'MMSZ4744AT1G', quantity: null },
    ]);
  });

  it.each([
    ['STM32F130ZBT6 x 500', 'STM32F130ZBT6', 500],
    ['STM32F130ZBT6 QTY: 500', 'STM32F130ZBT6', 500],
    ['500 pcs STM32F130ZBT6', 'STM32F130ZBT6', 500],
    ['STM32F130ZBT6, 500, target $2.50', 'STM32F130ZBT6', 500],
  ])('pulls a quantity out of %j', (line, mpn, quantity) => {
    expect(parse(line)).toEqual([{ input_string: mpn, quantity }]);
  });

  it('handles bulleted and numbered lists', () => {
    const items = parse('1. STM32F130ZBT6 x 10\n- MMSZ4744AT1G x 20\n* AD2736BRZ x 30');
    expect(items.map((i) => i.input_string)).toEqual([
      'STM32F130ZBT6', 'MMSZ4744AT1G', 'AD2736BRZ',
    ]);
    expect(items.map((i) => i.quantity)).toEqual([10, 20, 30]);
  });

  it('reads a thousands separator as one number, not two fields', () => {
    // Regression: splitting on a bare comma turned "1,000 pcs" into a second
    // column and left the part number as "SST25VF032B-75I  1".
    expect(parse('- SST25VF032B-75I  1,000 pcs')).toEqual([
      { input_string: 'SST25VF032B-75I', quantity: 1000 },
    ]);
  });

  it('keeps a comma that belongs to the part number', () => {
    // Regression: Nexperia really does ship BZX84-C10,215. A bare-comma split
    // silently truncated it to BZX84-C10.
    expect(parse('BZX84-C10,215')).toEqual([
      { input_string: 'BZX84-C10,215', quantity: null },
    ]);
  });

  it('does not turn prose into line items', () => {
    // Regression: these became rows in the results table.
    const { items, skipped } = parseRfqText(
      'Hi Jane,\nPlease quote the following, needed ASAP:\nSTM32F130ZBT6 x 5\nThanks,\nBob',
    );
    expect(items).toEqual([{ input_string: 'STM32F130ZBT6', quantity: 5 }]);
    expect(skipped).toContain('Please quote the following, needed ASAP:');
    expect(skipped).toContain('Bob');
  });

  it('strips quoted reply chains and signatures', () => {
    const { items } = parseRfqText(
      'STM32F130ZBT6\n> On Tue someone wrote:\n> MMSZ4744AT1G x 99\nRegards',
    );
    expect(items).toEqual([{ input_string: 'STM32F130ZBT6', quantity: null }]);
  });

  it('skips a header row rather than quoting it', () => {
    const { items } = parseRfqText('Part Number\tQty\nSTM32F130ZBT6\t250');
    expect(items).toEqual([{ input_string: 'STM32F130ZBT6', quantity: 250 }]);
  });

  it('handles tab-delimited paste from a spreadsheet', () => {
    expect(parse('STM32F130ZBT6\t500\tsome description')).toEqual([
      { input_string: 'STM32F130ZBT6', quantity: 500 },
    ]);
  });

  it('preserves messy part numbers for the matcher to clean up', () => {
    // Normalisation is the matcher's job; the parser must not pre-chew it.
    expect(parse('296-STM32F130C3Y6-ND x 500')).toEqual([
      { input_string: '296-STM32F130C3Y6-ND', quantity: 500 },
    ]);
  });

  it('reports every line it could not read', () => {
    const { items, skipped } = parseRfqText('lorem ipsum dolor\nSTM32F130ZBT6');
    expect(items).toHaveLength(1);
    expect(skipped).toContain('lorem ipsum dolor');
  });

  it('respects maxItems', () => {
    const text = Array.from({ length: 50 }, (_, i) => `STM32F130ZB${i}6`).join('\n');
    expect(parseRfqText(text, { maxItems: 10 }).items).toHaveLength(10);
  });

  it('returns nothing for empty input', () => {
    expect(parse('')).toEqual([]);
    expect(parse('\n\n   \n')).toEqual([]);
  });
});
