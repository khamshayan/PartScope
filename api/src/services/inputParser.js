/**
 * Turn raw RFQ text into line items.
 *
 * This is the PROVISIONAL parser. Phase 5 replaces it with proper email and
 * spreadsheet handling in the Python service -- signature stripping, quoted
 * reply chains, column inference from cell content. What is here covers the
 * common shapes well enough to exercise the pipeline end to end, and no more.
 *
 * Deliberately conservative: when a line cannot be confidently split, the whole
 * line is passed through as the input string. The matcher is good at coping
 * with noise, and a line dropped at this stage is invisible to the user.
 */

// Lines that are structure, not data.
const SKIP_PATTERNS = [
  /^\s*$/,
  /^[-=_*#]{3,}\s*$/,
  /^\s*(from|to|cc|bcc|sent|subject|date|regards|thanks|best|hi|hello|dear)\b[:,]?/i,
  /^\s*>/,                       // quoted reply
  /^\s*(part\s*(number|no|#)?|mpn|p\/n|component|description|qty|quantity)\s*$/i,
];

// A header row in a pasted table: several column names on one line.
const HEADER_ROW = /^\s*(part|mpn|p\/n|item|component)\b.*\b(qty|quantity|amount|price)\b/i;

const QUANTITY_PATTERNS = [
  /\bx\s*(\d[\d,]*)\b/i,             // MPN x 500
  /\bqty\s*[:=]?\s*(\d[\d,]*)\b/i,   // MPN QTY: 500
  /\b(\d[\d,]*)\s*(?:pcs|pieces|ea|units?)\b/i, // 500 pcs MPN
];

const toInt = (text) => {
  const parsed = Number.parseInt(String(text).replace(/,/g, ''), 10);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : null;
};

/** Does this token look like it could be a part number? */
function looksLikePartNumber(token) {
  if (token.length < 3 || token.length > 40) return false;
  if (!/[A-Za-z]/.test(token)) return false;
  if (!/\d/.test(token)) return false;
  if (/^\$/.test(token)) return false;
  return true;
}

function splitDelimited(line) {
  // Comma splitting uses "comma followed by whitespace", never a bare comma.
  // A bare comma destroys two things that legitimately contain one:
  //   - thousands separators:  "SST25VF032B-75I  1,000 pcs"
  //   - real part numbers:     "BZX84-C10,215"  (Nexperia packing code)
  // Requiring the space keeps both intact while still splitting pasted tables.
  for (const delimiter of ['\t', /;\s*/, /,\s+/, /\s{2,}/]) {
    const parts = line.split(delimiter).map((p) => p.trim()).filter(Boolean);
    if (parts.length > 1) return parts;
  }
  return [line.trim()];
}

function parseLine(line) {
  const cleaned = line.replace(/^\s*(?:[-*•]|\d+[.)])\s+/, '').trim();
  if (!cleaned) return null;

  const fields = splitDelimited(cleaned);

  // Delimited: first part-number-looking field wins, then look for a quantity
  // in the fields after it.
  if (fields.length > 1) {
    const mpnIndex = fields.findIndex(looksLikePartNumber);
    if (mpnIndex >= 0) {
      let quantity = null;
      for (const field of fields.slice(mpnIndex + 1)) {
        if (/^\d[\d,]*$/.test(field)) {
          quantity = toInt(field);
          break;
        }
        // "1,000 pcs" arrives as its own field once the line is split.
        const unit = field.match(/^(\d[\d,]*)\s*(?:pcs|pieces|ea|units?)$/i);
        if (unit) {
          quantity = toInt(unit[1]);
          break;
        }
      }
      return { input_string: fields[mpnIndex], quantity };
    }
  }

  // Free text: pull a quantity out of a known phrasing, leave the rest.
  for (const pattern of QUANTITY_PATTERNS) {
    const match = cleaned.match(pattern);
    if (match) {
      const remainder = cleaned.replace(match[0], ' ').replace(/\s{2,}/g, ' ').trim();
      const token = remainder.split(/\s+/).find(looksLikePartNumber);
      if (token) return { input_string: token, quantity: toInt(match[1]) };
    }
  }

  const token = cleaned.split(/\s+/).find(looksLikePartNumber);
  // No token in this line looks like a part number. Earlier this fell back to
  // passing the whole line through, which turned "Please quote the following:"
  // and the sender's name into line items -- a prose sentence in the results
  // table is worse than a line quietly reported as skipped.
  return token ? { input_string: token, quantity: null } : null;
}

export function parseRfqText(text, { maxItems = 200 } = {}) {
  const lines = String(text).split(/\r?\n/);
  const items = [];
  const skipped = [];

  for (const line of lines) {
    if (SKIP_PATTERNS.some((pattern) => pattern.test(line)) || HEADER_ROW.test(line)) {
      if (line.trim()) skipped.push(line.trim());
      continue;
    }
    const parsed = parseLine(line);
    if (parsed?.input_string) {
      items.push(parsed);
      if (items.length >= maxItems) break;
    } else if (line.trim()) {
      // Reported back to the caller so a line that should have been read is
      // visible rather than silently gone.
      skipped.push(line.trim());
    }
  }

  return { items, skipped };
}
