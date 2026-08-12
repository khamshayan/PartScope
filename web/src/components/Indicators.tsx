import type { VolatilityFlag } from '../api/types';

/** Prices span $0.01 resistors to $800 FPGAs, so precision has to follow scale. */
export function formatPrice(value: number | null | undefined): string {
  if (value === null || value === undefined) return '—';
  if (value >= 100) return `$${value.toFixed(0)}`;
  if (value >= 1) return `$${value.toFixed(2)}`;
  return `$${value.toFixed(4)}`;
}

/** Anything below this gets a Review tag; see the matcher's tier confidences. */
export const REVIEW_THRESHOLD = 0.7;

export function ConfidenceBar({ value, method }: { value: number; method: string }) {
  const pct = Math.round(value * 100);
  const needsReview = value > 0 && value < REVIEW_THRESHOLD;

  return (
    <div className="flex items-center gap-2">
      <div
        className="h-1.5 w-14 shrink-0 rounded-full bg-hairline"
        role="img"
        aria-label={`Match confidence ${pct} percent via ${method}`}
      >
        <div
          className={`h-full rounded-full ${needsReview ? 'bg-status-warn' : 'bg-accent'}`}
          style={{ width: `${Math.max(pct, 2)}%` }}
        />
      </div>
      <span className="tabular-nums text-xs text-ink-secondary">{pct}%</span>
      {needsReview && (
        <span className="rounded border border-status-warn/40 bg-status-warnSoft px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide text-status-warnInk">
          Review
        </span>
      )}
    </div>
  );
}

/**
 * The median is the number a buyer quotes; the P25–P75 range is the spread they
 * should expect to negotiate inside. Median leads, range supports.
 */
export function PriceBand({
  p25,
  p50,
  p75,
}: {
  p25: number | null;
  p50: number | null;
  p75: number | null;
}) {
  if (p50 === null) {
    return <span className="text-sm text-ink-muted">no price data</span>;
  }
  return (
    <div className="leading-tight">
      <div className="tabular-nums text-sm font-semibold text-ink">{formatPrice(p50)}</div>
      {/* The quote count lives in the expanded detail. In the row it pushed the
          range onto a third line, and a row three lines tall stops the table
          being scannable, which is the only thing this table is for. */}
      <div className="whitespace-nowrap tabular-nums text-xs text-ink-muted">
        {formatPrice(p25)} – {formatPrice(p75)}
      </div>
    </div>
  );
}

/**
 * Forecast against today's median. The absolute number is what gets quoted;
 * the delta is what tells a buyer whether to move now or wait a week, so both
 * are shown and neither needs the model name repeated on every row.
 */
export function ForecastCell({
  forecast,
  median,
}: {
  forecast: number | null;
  median: number | null;
}) {
  if (forecast === null) return <span className="text-sm text-ink-muted">—</span>;

  const delta = median && median > 0 ? (forecast - median) / median : null;
  const meaningful = delta !== null && Math.abs(delta) >= 0.005;

  return (
    <div className="leading-tight">
      <div className="tabular-nums text-sm text-ink">{formatPrice(forecast)}</div>
      <div className="whitespace-nowrap tabular-nums text-xs text-ink-muted">
        {meaningful ? (
          <span className={delta > 0 ? 'text-status-badInk' : 'text-status-goodInk'}>
            {delta > 0 ? '+' : ''}
            {(delta * 100).toFixed(1)}%
          </span>
        ) : (
          'flat'
        )}
        <span className="ml-1">next wk</span>
      </div>
    </div>
  );
}

const HEAT_STYLES: Record<VolatilityFlag, { dot: string; chip: string; label: string }> = {
  STABLE: {
    dot: 'bg-status-good',
    chip: 'bg-status-goodSoft text-status-goodInk border-status-good/30',
    label: 'Stable',
  },
  ELEVATED: {
    dot: 'bg-status-warn',
    chip: 'bg-status-warnSoft text-status-warnInk border-status-warn/40',
    label: 'Elevated',
  },
  VOLATILE: {
    dot: 'bg-status-bad',
    chip: 'bg-status-badSoft text-status-badInk border-status-bad/30',
    label: 'Volatile',
  },
};

/**
 * Status colour never carries the meaning alone -- the chip always shows a dot
 * AND the word. Amber at this step is deliberately below 3:1 against a light
 * surface, so the text is what a low-vision or colourblind reader relies on.
 */
export function HeatChip({
  flag,
  heatIndex,
}: {
  flag: VolatilityFlag | null;
  heatIndex: number | null;
}) {
  if (!flag) return <span className="text-sm text-ink-muted">—</span>;
  const style = HEAT_STYLES[flag];

  return (
    <div className="flex items-center gap-2">
      <span
        className={`inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-xs font-medium ${style.chip}`}
        title={`Quote dispersion is ${heatIndex?.toFixed(2)}× this part's own 52-week baseline`}
      >
        <span className={`h-1.5 w-1.5 rounded-full ${style.dot}`} aria-hidden />
        {style.label}
      </span>
      {heatIndex !== null && (
        <span className="tabular-nums text-xs text-ink-muted">{heatIndex.toFixed(2)}×</span>
      )}
    </div>
  );
}

export function LifecyclePill({ status }: { status: string | null }) {
  if (!status) return null;
  const dead = status === 'Obsolete' || status === 'EOL';
  return (
    <span
      className={`rounded px-1.5 py-0.5 text-[11px] font-medium ${
        dead
          ? 'bg-status-badSoft text-status-badInk'
          : status === 'NRND'
            ? 'bg-status-warnSoft text-status-warnInk'
            : 'bg-page text-ink-secondary'
      }`}
    >
      {status}
    </span>
  );
}
