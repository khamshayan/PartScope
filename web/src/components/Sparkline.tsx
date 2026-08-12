import { Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, YAxis } from 'recharts';

import { formatPrice } from './Indicators';

/**
 * 52 weeks of median price for one part.
 *
 * A single series, so no legend -- the heading names it. Thin 2px mark in the
 * accent hue, no axes or gridlines: at this size a reader is being asked "what
 * shape is this", not "what was it in week 31". The tooltip is there for the
 * follow-up question.
 */
export function Sparkline({ values }: { values: number[] }) {
  if (values.length < 2) {
    return (
      <div className="flex h-24 items-center justify-center text-xs text-ink-muted">
        not enough price history to plot
      </div>
    );
  }

  const data = values.map((price, index) => ({
    week: index - (values.length - 1),
    price,
  }));
  const low = Math.min(...values);
  const high = Math.max(...values);
  const first = values[0];
  const last = values[values.length - 1];
  const change = first > 0 ? (last - first) / first : 0;

  return (
    <div>
      <div className="mb-1 flex items-baseline justify-between">
        <h4 className="text-xs font-medium text-ink-secondary">
          Median broker price, last {values.length} weeks
        </h4>
        <span className="tabular-nums text-xs text-ink-muted">
          {formatPrice(low)} – {formatPrice(high)}
          <span className={`ml-2 ${change >= 0 ? 'text-status-badInk' : 'text-status-goodInk'}`}>
            {change >= 0 ? '+' : ''}
            {(change * 100).toFixed(0)}%
          </span>
        </span>
      </div>

      <div className="h-24 w-full">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={data} margin={{ top: 4, right: 4, bottom: 0, left: 0 }}>
            {/* Domain padded so a flat series does not render as a line pinned
                to the frame edge. */}
            <YAxis hide domain={[low * 0.96, high * 1.04]} />
            <ReferenceLine y={first} stroke="#e1e0d9" strokeDasharray="3 3" />
            <Tooltip
              cursor={{ stroke: '#c3c2b7', strokeWidth: 1 }}
              contentStyle={{
                border: '1px solid #e1e0d9',
                borderRadius: 6,
                fontSize: 12,
                padding: '4px 8px',
                boxShadow: 'none',
              }}
              labelFormatter={(week) => (week === 0 ? 'this week' : `${week} weeks`)}
              formatter={(value: number) => [formatPrice(value), 'median']}
            />
            <Line
              type="monotone"
              dataKey="price"
              stroke="#2a78d6"
              strokeWidth={2}
              dot={false}
              activeDot={{ r: 4, strokeWidth: 0 }}
              isAnimationActive={false}
            />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
