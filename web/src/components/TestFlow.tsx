import type { LineItem } from '../api/types';

/**
 * The test-flow recommendation, shown with its arithmetic.
 *
 * The score is deliberately not the headline. A buyer is about to spend money
 * and lab time on this, and their supplier will push back, so what they need in
 * front of them is the list of reasons -- "Obsolete +30, no authorized stock
 * +20" -- not a number they would have to take on trust. That is the entire
 * reason this step is rules-based rather than learned.
 */

const BAND_STYLES: Record<string, { chip: string; bar: string }> = {
  standard: {
    chip: 'bg-status-goodSoft text-status-goodInk border-status-good/30',
    bar: 'bg-status-good',
  },
  enhanced: {
    chip: 'bg-status-warnSoft text-status-warnInk border-status-warn/40',
    bar: 'bg-status-warn',
  },
  full: {
    chip: 'bg-status-badSoft text-status-badInk border-status-bad/30',
    bar: 'bg-status-bad',
  },
};

export function TestFlow({ item }: { item: LineItem }) {
  if (item.risk_score === null || !item.test_recommendation) {
    return (
      <div className="text-xs text-ink-muted">
        No test recommendation — identify the part first.
      </div>
    );
  }

  const style = BAND_STYLES[item.test_band ?? 'standard'] ?? BAND_STYLES.standard;
  const target = item.target_turnaround_hours ?? 48;
  const hours = item.est_turnaround_hours ?? 0;
  const share = Math.min(hours / target, 1);

  return (
    <div>
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <h4 className="text-xs font-medium text-ink-secondary">Counterfeit test flow</h4>
        <span
          className={`inline-flex items-center rounded-full border px-2 py-0.5 text-xs font-medium ${style.chip}`}
        >
          {item.test_recommendation}
        </span>
        <span className="tabular-nums text-xs text-ink-muted">
          risk {item.risk_score}/100
        </span>
      </div>

      {/* The arithmetic, itemised. */}
      {item.risk_reasons.length === 0 ? (
        <p className="text-xs text-ink-secondary">
          No risk signals fired — current part, authorized stock available, calm
          market, confident match.
        </p>
      ) : (
        <ul className="space-y-1">
          {item.risk_reasons.map((reason) => (
            <li key={reason.code} className="flex items-baseline gap-2 text-xs">
              <span className="w-7 shrink-0 tabular-nums text-right font-medium text-ink">
                +{reason.points}
              </span>
              <span>
                {/* The label must not wrap mid-phrase -- "No authorized / stock"
                    across three lines makes the breakdown hard to scan, which
                    defeats the point of itemising it. */}
                <span className="whitespace-nowrap font-medium text-ink">{reason.label}</span>
                <span className="ml-1.5 text-ink-muted">{reason.detail}</span>
              </span>
            </li>
          ))}
          <li className="flex items-baseline gap-2 border-t border-hairline pt-1 text-xs">
            <span className="w-7 shrink-0 tabular-nums text-right font-semibold text-ink">
              {item.risk_score}
            </span>
            <span className="font-medium text-ink">
              total → {item.test_recommendation}
            </span>
          </li>
        </ul>
      )}

      {/* Turnaround against the target a buyer is usually working to. */}
      <div className="mt-3">
        <div className="flex items-baseline justify-between text-xs">
          <span className="text-ink-secondary">Estimated turnaround</span>
          <span className="tabular-nums text-ink">
            {hours} h
            <span className="ml-1 text-ink-muted">of {target} h target</span>
          </span>
        </div>
        <div className="mt-1 h-1.5 w-full rounded-full bg-hairline">
          <div
            className={`h-full rounded-full ${style.bar}`}
            style={{ width: `${Math.max(share * 100, 3)}%` }}
          />
        </div>
        {item.approaching_target && (
          <p className="mt-1 text-xs text-status-warnInk">
            {item.exceeds_target
              ? `Exceeds the ${target}-hour target — agree an extension before committing.`
              : `Uses ${Math.round(share * 100)}% of the ${target}-hour target — little room for a re-test.`}
          </p>
        )}
      </div>

      <details className="mt-3">
        <summary className="cursor-pointer text-xs text-ink-secondary">
          {item.test_methods.length} test methods
        </summary>
        <ul className="mt-1 space-y-0.5">
          {item.test_methods.map((method) => (
            <li key={method} className="text-xs text-ink-muted">
              · {method}
            </li>
          ))}
        </ul>
      </details>
    </div>
  );
}

/** Compact chip for the results row. */
export function TestFlowChip({ item }: { item: LineItem }) {
  if (item.risk_score === null || !item.test_recommendation) {
    return <span className="text-sm text-ink-muted">—</span>;
  }
  const style = BAND_STYLES[item.test_band ?? 'standard'] ?? BAND_STYLES.standard;
  return (
    <div className="leading-tight">
      <span
        className={`inline-flex items-center whitespace-nowrap rounded-full border px-2 py-0.5 text-xs font-medium ${style.chip}`}
      >
        {item.test_recommendation}
      </span>
      <div className="whitespace-nowrap tabular-nums text-xs text-ink-muted">
        risk {item.risk_score} · {item.est_turnaround_hours} h
      </div>
    </div>
  );
}
