/**
 * The "Load example" payloads.
 *
 * Five RFQs rather than one, picked at random per click, so a demo does not
 * replay the same rows every time and so the matcher gets shown against more
 * than one house style of RFQ.
 *
 * Every part number here is a real one, present in the catalog -- the catalog
 * is now Mouser data rather than generated, so an invented MPN would simply
 * come back unmatched and demonstrate nothing.
 *
 * Each example is deliberately messy in the ways real RFQs are, and the mess is
 * distributed so no single example carries all of it: distributor order codes
 * (Mouser's numeric prefixes and Digi-Key's -ND suffixes), lowercase pastes,
 * O-for-zero typos, spaces inserted inside a passive's part number, truncated
 * suffixes, tab-separated spreadsheet paste, a part number containing a comma,
 * email headers, signature blocks and quoted reply chains. Each one exercises a
 * different branch of the matcher.
 *
 * Every example also carries at least one part that does not exist and cannot
 * be salvaged, so the no-match path and its near-miss suggestions stay visible.
 *
 * On the risk rows: real distributor data is overwhelmingly healthy -- of 6,286
 * parts, 6,218 are Active and only nine have no authorized stock -- so the
 * end-of-life and zero-stock lines below are the specific real parts that carry
 * those states (the NXP MC9S08 family, the onsemi and Vishay FETs, the Murata
 * GCQ0335 caps, Crystek's CCHD-950). They are named deliberately: an example
 * where every row came back Active and in stock would hide the two features the
 * product is built around.
 */

/** Urgent line-down email: mixed categories, a truncation, an EOL FET. */
const URGENT_LINE_DOWN = `From: dana.reyes@acme-aero.example
Subject: RFQ 41822 — urgent, line down

Hi,

Please quote the following for delivery ASAP:

1. 511-LDO40LPURY x 2,500
2. erj-pc3b2001v  10000
3. KGF32L CG2J333JL   4,000 pcs
4. SCTHC250N120G3	40
- 579-AVR32LA14-I/ST x 600
- ASE7-25.000MHZ-LC-T  1,200
- ZZQQ99NOTAPART x 5

Also these two, we are told they are getting tight:
MC9S08QE64CLC x 250
sqj850ep-t2_ge3, 500

Let me know on lead times.

Thanks,
Dana
> On Tuesday, procurement wrote:
> please also check the obsolete lines
`;

/** Bare paste out of a spreadsheet: tabs, no prose, an O-for-zero typo. */
const SPREADSHEET_PASTE = `ERJ-PC3B2OO1V	25000
RCA040222K0JNED	100,000
UCR006YVPFLR680	5000
MSAST105SB7103KFNA01	12,000
GRM188R60J106ME84D	50000
LDO40LPU33RY	1500
PIC18F26Q35-E/SS	400
FT3MNTUM-32.0-T1	800
QQ00-NO-SUCH-PART	100
`;

/** Obsolescence sweep: an engineer working a last-time-buy list. */
const LAST_TIME_BUY = `From: mchen@northfield-controls.example
Subject: RE: last-time-buy list — need pricing by Friday

Following up on the EOL notices. Quantities are full lifetime buy:

  MC9S08AC96CFGE       1,200
  mc9s08jm32cgt        900
  MC9S08FL8CLC         600
  FDMC86520DC          2,000
  FDMS8050ET30         1,500
  10TPB330MW           8,000
  6TPF220M6L           4,000
  XCF77Q-DISCONTINUED  250

If any of these are already gone, send alternates — form/fit/function only,
we cannot requalify the board this year.

Michael Chen
Northfield Controls | Supply Chain
> > we were told the S08 line goes end of life in Q3
> > and the onsemi parts are already on allocation
`;

/** Terse broker-style list: distributor codes, a comma in an MPN, no prose. */
const BROKER_LIST = `need pricing + lead time, all RoHS:

667-ERJ-PC3B1001V x 20,000
81-KGF32LCG2J333JL 3000
TPM1R408RH,LQ  750
ISC016N08NM8SCATMA1 x 1,100
sqj461ep-t1_ne3 2,200
815-ASDTDV-24.000MHZ-LY-T 5,000
AP7369Q-50E-13 x 900
NEX91730PB-Q1OOY 300
BOGUS123FAKE x 50
`;

/** Aerospace build: defense-grade parts and the zero-stock lines. */
const AEROSPACE_BUILD = `From: procurement@kestrel-avionics.example
Subject: Kestrel build 7 — flight hardware, quote requested

All lines flight-qualified, full traceability and CoC required.

M39014/05-2046 qty 400
LEO3910PDT-D    qty 60
CCHD-950-25-60.000	x 120
GCQ0335C1H100JB01D  x 10,000
gcq0335c1h200gb01d, 10000
CL03Z105MQR6PNC     25,000
RC1210FR-0716KL     8,000
ASE7-25.000MHZ-LC-T x 500
NOPART-000-XX x 10

Flag anything with no authorized stock — we will need a source
qualification package before we can buy from the open market.

Regards,
Procurement, Kestrel Avionics
> On Monday, quality wrote:
> nothing from a broker without test data, please
`;

export const SAMPLE_RFQS: string[] = [
  URGENT_LINE_DOWN,
  SPREADSHEET_PASTE,
  LAST_TIME_BUY,
  BROKER_LIST,
  AEROSPACE_BUILD,
];

/**
 * One example at random.
 *
 * Genuinely random rather than a rotation, so repeats happen -- a cycle would
 * make a demo predictable in a way that quietly implies the five are a sequence
 * meant to be read in order.
 */
export function pickSampleRfq(): string {
  return SAMPLE_RFQS[Math.floor(Math.random() * SAMPLE_RFQS.length)];
}
