/**
 * The "Load example" payload.
 *
 * Deliberately messy in the ways real RFQs are: a distributor order code, a
 * lowercase paste, an O-for-zero typo, spaces inside a passive's part number,
 * a truncation, a part that does not exist, an email signature and a quoted
 * reply chain. Every one of these exercises a different branch of the matcher.
 *
 * The last five lines are chosen to make the product's actual point visible.
 * Three have a genuinely disturbed market right now -- an obsolete power FET
 * mid-shortage, an obsolete regulator and an EOL op-amp -- and two are
 * end-of-life defense-grade parts with no authorized stock, which is the
 * combination that routes to a full AS6171 test flow.
 *
 * An example RFQ where every row came back green and Standard would demo the
 * matcher and hide both features the product is built around.
 */
export const SAMPLE_RFQ = `From: buyer@acme-aero.example
Subject: RFQ 41822 — urgent, line down

Hi,

Please quote the following for delivery ASAP:

1. 296-STM32F130C3Y6-ND x 500
2. stm32f105kct7, 250, target $3.10
3. MMSZ4744ATIG QTY: 2000
4. D38999/24JA103SN	12
- CRCW 0603 100K JKEA  10,000 pcs
- SST25VF032B-75I  1,000 pcs
- AD2736BRZ-REEL x 40
- MCP1827-0180E x 75
- BZX84-C10,215 x 5000
- ZZQQ99NOTAPART x 5

Also these three, we are told they are getting tight:
PSMN10V4-60YL-TR x 250
adp6208armz-2.5, 100
MCP6001-E/SN  500 pcs

For the Kestrel build standard, both flight-qualified:
m39014/11-1123v qty 40
M2GL250T-FG676 x 6

Let me know on lead times.

Thanks,
Dana
> On Tuesday, procurement wrote:
> please also check the obsolete lines
`;
