import { useState } from 'react';

import type { LineItem, NearMiss } from '../api/types';
import { ConfidenceBar, ForecastCell, HeatChip, LifecyclePill, PriceBand } from './Indicators';
import { Sparkline } from './Sparkline';
import { TestFlow, TestFlowChip } from './TestFlow';

const METHOD_LABELS: Record<string, string> = {
  exact: 'Exact match',
  confusable_variant: 'O/0 or I/1 variant',
  prefix: 'Truncated input',
  base_part: 'Base part (grade suffix differs)',
  fuzzy: 'Fuzzy match',
  no_match: 'No match',
};

interface Props {
  item: LineItem;
  index: number;
  onAcceptSuggestion: (index: number, mpn: string) => void;
  resolving: boolean;
}

export function ResultRow({ item, index, onAcceptSuggestion, resolving }: Props) {
  const [expanded, setExpanded] = useState(false);
  const noMatch = !item.matched_mpn;

  return (
    <>
      <tr
        onClick={() => setExpanded((open) => !open)}
        className={`cursor-pointer border-t border-hairline align-top transition-colors ${
          noMatch ? 'bg-status-warnSoft/50 hover:bg-status-warnSoft' : 'hover:bg-page'
        }`}
      >
        <td className="px-3 py-2.5">
          <div className="font-mono text-[13px] text-ink">{item.input_string}</div>
          {item.quantity !== null && (
            <div className="text-xs text-ink-muted">qty {item.quantity.toLocaleString()}</div>
          )}
        </td>

        <td className="px-3 py-2.5">
          {noMatch ? (
            <span className="text-sm font-medium text-status-warnInk">No match</span>
          ) : (
            <>
              <div className="font-mono text-[13px] font-medium text-ink">{item.matched_mpn}</div>
              <div className="flex items-center gap-1.5 text-xs text-ink-muted">
                <span>{item.manufacturer}</span>
                <LifecyclePill status={item.lifecycle_status} />
              </div>
            </>
          )}
        </td>

        <td className="px-3 py-2.5">
          {noMatch ? (
            <span className="text-xs text-ink-muted">—</span>
          ) : (
            <ConfidenceBar value={item.confidence} method={item.match_method} />
          )}
        </td>

        <td className="px-3 py-2.5">
          <PriceBand p25={item.price_p25} p50={item.price_p50} p75={item.price_p75} />
        </td>

        <td className="px-3 py-2.5">
          <ForecastCell forecast={item.forecast_price} median={item.price_p50} />
        </td>

        <td className="px-3 py-2.5">
          <HeatChip flag={item.volatility_flag} heatIndex={item.heat_index} />
        </td>

        <td className="px-3 py-2.5">
          <TestFlowChip item={item} />
        </td>

        <td className="px-3 py-2.5 text-right">
          <span className="text-xs font-medium text-accent">
            {expanded ? 'Hide' : 'Details'}
          </span>
        </td>
      </tr>

      {noMatch && item.near_misses.length > 0 && (
        <tr className="bg-status-warnSoft/50">
          <td colSpan={8} className="px-3 pb-3 pt-0">
            <div className="flex flex-wrap items-center gap-2 text-xs">
              <span className="text-status-warnInk">Did you mean</span>
              {item.near_misses.map((miss: NearMiss) => (
                <button
                  key={miss.mpn}
                  type="button"
                  disabled={resolving}
                  onClick={() => onAcceptSuggestion(index, miss.mpn)}
                  className="rounded border border-status-warn/40 bg-surface px-2 py-1 font-mono text-[12px] text-ink hover:border-accent hover:text-accent disabled:opacity-50"
                  title={`${miss.manufacturer} — ${miss.description}`}
                >
                  {miss.mpn}
                </button>
              ))}
              {resolving && <span className="text-ink-muted">resolving…</span>}
            </div>
          </td>
        </tr>
      )}

      {expanded && (
        <tr className="border-t border-hairline bg-page">
          <td colSpan={8} className="px-3 py-4">
            <div className="grid gap-6 lg:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)]">
              <div>
                {item.sparkline.length > 1 ? (
                  <Sparkline values={item.sparkline} />
                ) : (
                  <div className="text-xs text-ink-muted">No price history for this line.</div>
                )}

                <dl className="mt-4 grid grid-cols-2 gap-x-6 gap-y-2 text-xs sm:grid-cols-3">
                  <Fact label="Match method" value={METHOD_LABELS[item.match_method] ?? item.match_method} />
                  <Fact label="Lifecycle" value={item.lifecycle_status ?? '—'} />
                  <Fact
                    label="Authorized stock"
                    value={
                      item.authorized_stock === null
                        ? '—'
                        : item.authorized_stock === 0
                          ? 'none'
                          : item.authorized_stock.toLocaleString()
                    }
                  />
                  <Fact label="Category" value={item.category ?? '—'} />
                  <Fact label="Package" value={item.catalog?.package ?? '—'} />
                  <Fact
                    label="Introduced"
                    value={item.date_introduced ? item.date_introduced.slice(0, 4) : '—'}
                  />
                  <Fact label="Normalized key" value={item.normalized_key} mono />
                  <Fact
                    label="Rules applied"
                    value={item.rules_applied.length ? item.rules_applied.join(', ') : 'none'}
                  />
                  <Fact
                    label="Price band basis"
                    value={
                      item.quote_count > 0
                        ? `${item.quote_count} quotes · ${item.weeks_of_history} wks history`
                        : 'no quotes'
                    }
                  />
                  <Fact label="Forecast model" value={item.forecast_model ?? '—'} />
                </dl>

                {item.description && (
                  <p className="mt-3 text-xs text-ink-secondary">{item.description}</p>
                )}
              </div>

              <div className="space-y-5">
                <TestFlow item={item} />

                <h4 className="mb-2 text-xs font-medium text-ink-secondary">
                  Cross-manufacturer alternates
                </h4>
                {item.alternates.length === 0 ? (
                  <p className="text-xs text-ink-muted">None found in this category.</p>
                ) : (
                  <ul className="space-y-1.5">
                    {item.alternates.map((alt) => (
                      <li
                        key={alt.mpn}
                        className="rounded border border-hairline bg-surface px-2 py-1.5"
                      >
                        <div className="flex items-center justify-between gap-2">
                          <span className="font-mono text-[12px] text-ink">{alt.mpn}</span>
                          <span className="tabular-nums text-[11px] text-ink-muted">
                            {Math.round(alt.similarity * 100)}%
                          </span>
                        </div>
                        <div className="flex items-center gap-1.5 text-[11px] text-ink-muted">
                          <span>{alt.manufacturer}</span>
                          <LifecyclePill status={alt.lifecycle_status} />
                        </div>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            </div>
          </td>
        </tr>
      )}
    </>
  );
}

function Fact({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div>
      <dt className="text-ink-muted">{label}</dt>
      <dd className={`text-ink ${mono ? 'font-mono text-[11px]' : ''}`}>{value}</dd>
    </div>
  );
}
