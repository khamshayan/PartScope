import type { ParserDiagnostics, RfqResponse } from '../api/types';
import { ApiFailure } from '../api/client';
import { ResultRow } from './ResultRow';

const COLUMNS = [
  'Input',
  'Matched Part',
  'Confidence',
  'Price Band',
  'Forecast',
  'Market Heat',
  '',
];

interface Props {
  result: RfqResponse | null;
  loading: boolean;
  error: ApiFailure | null;
  onAcceptSuggestion: (index: number, mpn: string) => void;
  resolvingIndex: number | null;
  onRetry: () => void;
}

export function ResultsPanel({
  result,
  loading,
  error,
  onAcceptSuggestion,
  resolvingIndex,
  onRetry,
}: Props) {
  return (
    <section className="flex h-full min-h-0 flex-col rounded-lg border border-hairline bg-surface">
      <Header result={result} loading={loading} />

      <div className="min-h-0 flex-1 overflow-auto">
        {error ? (
          <ErrorState error={error} onRetry={onRetry} />
        ) : loading && !result ? (
          <Table>
            <SkeletonRows />
          </Table>
        ) : !result ? (
          <EmptyState />
        ) : (
          <>
            <Table>
              {result.items.map((item, index) => (
                <ResultRow
                  key={`${item.input_string}-${index}`}
                  item={item}
                  index={index}
                  onAcceptSuggestion={onAcceptSuggestion}
                  resolving={resolvingIndex === index}
                />
              ))}
            </Table>
            {result.parser?.parser === 'spreadsheet' && (
              <ParserNote parser={result.parser} />
            )}
            {result.skipped_lines.length > 0 && <SkippedLines lines={result.skipped_lines} />}
          </>
        )}
      </div>
    </section>
  );
}

function Header({ result, loading }: { result: RfqResponse | null; loading: boolean }) {
  const matched = result?.items.filter((item) => item.matched_mpn).length ?? 0;
  const review =
    result?.items.filter((item) => item.matched_mpn && item.confidence < 0.7).length ?? 0;
  const hot =
    result?.items.filter((item) => item.volatility_flag && item.volatility_flag !== 'STABLE')
      .length ?? 0;

  return (
    <div className="flex flex-wrap items-baseline justify-between gap-2 border-b border-hairline px-4 py-3">
      <h2 className="text-sm font-medium text-ink">Results</h2>
      {result && !loading && (
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-ink-secondary">
          <span>
            <strong className="tabular-nums text-ink">{matched}</strong> of{' '}
            <strong className="tabular-nums text-ink">{result.count}</strong> matched
          </span>
          {review > 0 && (
            <span className="text-status-warnInk">{review} need review</span>
          )}
          {hot > 0 && <span className="text-status-badInk">{hot} above baseline heat</span>}
          <span className="text-ink-muted">
            {result.model} · {Math.round(result.elapsed_ms)} ms
          </span>
        </div>
      )}
    </div>
  );
}

function Table({ children }: { children: React.ReactNode }) {
  return (
    <table className="w-full border-collapse text-left">
      <thead className="sticky top-0 z-10 bg-surface">
        <tr className="border-b border-hairline">
          {COLUMNS.map((column) => (
            <th
              key={column}
              scope="col"
              className="px-3 py-2 text-[11px] font-medium uppercase tracking-wide text-ink-muted"
            >
              {column}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>{children}</tbody>
    </table>
  );
}

/**
 * Skeleton rows, not a spinner: the table keeps its shape while it loads, so
 * nothing jumps when the data lands and the wait reads as "nearly there"
 * rather than "something is happening somewhere".
 */
function SkeletonRows() {
  return (
    <>
      {Array.from({ length: 6 }).map((_, row) => (
        <tr key={row} className="border-t border-hairline">
          {Array.from({ length: 7 }).map((__, cell) => (
            <td key={cell} className="px-3 py-3">
              <div
                className="h-3 rounded bg-hairline/70"
                style={{ width: `${[70, 80, 55, 60, 50, 65, 30][cell]}%` }}
              />
            </td>
          ))}
        </tr>
      ))}
    </>
  );
}

function EmptyState() {
  return (
    <div className="flex h-full flex-col items-center justify-center px-6 py-16 text-center">
      <p className="text-sm font-medium text-ink">No RFQ analysed yet</p>
      <p className="mt-1 max-w-sm text-xs text-ink-secondary">
        Paste part numbers on the left and press Analyze. Messy input is expected —
        distributor prefixes, reel suffixes, typos and truncations are all handled.
      </p>
      <p className="mt-3 text-xs text-ink-muted">
        Try <strong className="font-medium text-ink-secondary">Load example</strong> for a
        realistic one.
      </p>
    </div>
  );
}

function ErrorState({ error, onRetry }: { error: ApiFailure; onRetry: () => void }) {
  return (
    <div className="flex h-full flex-col items-center justify-center px-6 py-16 text-center">
      <span className="rounded-full border border-status-bad/30 bg-status-badSoft px-2 py-0.5 text-[11px] font-medium uppercase tracking-wide text-status-badInk">
        {error.code.replace(/_/g, ' ')}
      </span>
      <p className="mt-3 max-w-md text-sm font-medium text-ink">{error.message}</p>
      {error.hint && <p className="mt-1 max-w-md text-xs text-ink-secondary">{error.hint}</p>}
      <button
        type="button"
        onClick={onRetry}
        className="mt-4 rounded-md border border-hairline bg-surface px-3 py-1.5 text-xs font-medium text-ink hover:border-accent hover:text-accent"
      >
        Try again
      </button>
    </div>
  );
}

/**
 * Lines the parser could not read are surfaced rather than dropped. A line that
 * silently vanished between the paste and the table is the one failure mode a
 * buyer would never catch.
 */
/**
 * What the spreadsheet parser decided, in plain language.
 *
 * Columns are inferred from cell content rather than header text, which works
 * well and is completely opaque when it goes wrong. Stating the choice lets a
 * buyer notice that the "quantity" column was actually the line number before
 * they order against it.
 */
function ParserNote({ parser }: { parser: ParserDiagnostics }) {
  const sheets = parser.sheets_considered ?? [];
  const describe = (letter?: string | null, header?: string | null) =>
    letter ? `${letter}${header ? ` (“${header}”)` : ''}` : null;

  const part = describe(parser.part_column, parser.part_column_header);
  const quantity = describe(parser.quantity_column, parser.quantity_column_header);

  return (
    <div className="border-t border-hairline bg-accent-soft/40 px-4 py-3 text-xs text-ink-secondary">
      Read sheet <strong className="font-medium text-ink">{parser.sheet}</strong>
      {typeof parser.header_row === 'number' && parser.header_row > 0 && (
        <>, data from row {parser.header_row + 1}</>
      )}
      {part && (
        <> · parts from column <strong className="font-medium text-ink">{part}</strong></>
      )}
      {quantity ? (
        <> · quantities from column <strong className="font-medium text-ink">{quantity}</strong></>
      ) : (
        <> · no quantity column found</>
      )}
      {sheets.length > 1 && (
        <div className="mt-1 text-ink-muted">
          Other sheets checked:{' '}
          {sheets
            .filter((sheet) => sheet.sheet !== parser.sheet)
            .map((sheet) => `${sheet.sheet} (${sheet.items} parts)`)
            .join(', ')}
        </div>
      )}
    </div>
  );
}

function SkippedLines({ lines }: { lines: string[] }) {
  return (
    <details className="border-t border-hairline px-4 py-3">
      <summary className="cursor-pointer text-xs text-ink-secondary">
        {lines.length} line{lines.length === 1 ? '' : 's'} ignored (headers, prose, quoted replies)
      </summary>
      <ul className="mt-2 space-y-0.5">
        {lines.map((line, index) => (
          <li key={index} className="font-mono text-[11px] text-ink-muted">
            {line}
          </li>
        ))}
      </ul>
    </details>
  );
}
