import { useCallback, useRef, useState } from 'react';

import { ApiFailure, analyzeFile, analyzeText, fetchPart } from './api/client';
import type { LineItem, RfqResponse } from './api/types';
import { ResultsPanel } from './components/ResultsPanel';
import { RfqInput } from './components/RfqInput';

export default function App() {
  const [text, setText] = useState('');
  const [result, setResult] = useState<RfqResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<ApiFailure | null>(null);
  const [resolvingIndex, setResolvingIndex] = useState<number | null>(null);
  const [uploadedFile, setUploadedFile] = useState<File | null>(null);

  // Lets a slow analyse be cancelled, and stops an abandoned request from
  // landing on top of a newer one.
  const inFlight = useRef<AbortController | null>(null);

  const run = useCallback(async (task: (signal: AbortSignal) => Promise<RfqResponse>) => {
    inFlight.current?.abort();
    const controller = new AbortController();
    inFlight.current = controller;

    setLoading(true);
    setError(null);
    try {
      setResult(await task(controller.signal));
    } catch (caught) {
      if ((caught as Error)?.name === 'AbortError') return;
      setError(
        caught instanceof ApiFailure
          ? caught
          : new ApiFailure('Something went wrong.', 'UNKNOWN', 0),
      );
      setResult(null);
    } finally {
      if (inFlight.current === controller) {
        inFlight.current = null;
        setLoading(false);
      }
    }
  }, []);

  // A spreadsheet's bytes are a zip archive. Mirroring them into the textarea
  // filled it with binary garbage, so only text formats get mirrored -- and a
  // spreadsheet is instead remembered as a file, shown as a chip.
  const TEXT_LIKE = /\.(txt|csv|eml|md|log)$/i;

  const handleAnalyze = useCallback(() => {
    // Edited text wins over the uploaded file; an untouched upload re-sends the
    // file, because its bytes are the only complete record of what it holds.
    if (text.trim()) {
      void run((signal) => analyzeText(text, signal));
      return;
    }
    if (uploadedFile) {
      void run((signal) => analyzeFile(uploadedFile, signal));
    }
  }, [run, text, uploadedFile]);

  const handleFile = useCallback(
    (file: File) => {
      setUploadedFile(file);
      void run(async (signal) => {
        const response = await analyzeFile(file, signal);
        setText(TEXT_LIKE.test(file.name) ? await file.text().catch(() => '') : '');
        return response;
      });
    },
    [run],
  );

  const handleCancel = useCallback(() => {
    inFlight.current?.abort();
    inFlight.current = null;
    setLoading(false);
  }, []);

  /**
   * A buyer accepting one of the near-miss suggestions on a no-match row.
   * Patches that row in place rather than re-analysing the whole RFQ, which
   * would persist a second RFQ and throw away every row they had already read.
   */
  const handleAcceptSuggestion = useCallback(
    async (index: number, mpn: string) => {
      setResolvingIndex(index);
      try {
        const detail = await fetchPart(mpn);
        setResult((previous) => {
          if (!previous) return previous;
          const items = [...previous.items];
          const original = items[index];
          items[index] = {
            ...original,
            ...(detail as Partial<LineItem>),
            input_string: original.input_string,
            quantity: original.quantity,
            matched_mpn: detail.mpn,
            // Not a matcher result: a human chose this. Say so rather than
            // inventing a confidence score for someone else's judgement.
            confidence: 1,
            match_method: 'exact',
            near_misses: [],
          } as LineItem;
          return { ...previous, items };
        });
      } catch (caught) {
        setError(
          caught instanceof ApiFailure
            ? caught
            : new ApiFailure('Could not load that part.', 'UNKNOWN', 0),
        );
      } finally {
        setResolvingIndex(null);
      }
    },
    [],
  );

  return (
    <div className="flex min-h-screen flex-col bg-page text-ink">
      <header className="border-b border-hairline bg-surface">
        <div className="mx-auto max-w-[1500px] px-6 py-4">
          <div className="flex flex-wrap items-baseline justify-between gap-2">
            <div>
              <h1 className="text-lg font-semibold tracking-tight text-ink">Source Scope</h1>
              <p className="text-sm text-ink-secondary">
                RFQ triage and price-band recommendation
              </p>
            </div>
            <a
              className="text-xs text-ink-muted hover:text-accent"
              href="https://github.com"
              onClick={(event) => event.preventDefault()}
            >
              secondary-market component sourcing
            </a>
          </div>
        </div>

        {/* Persistent, not dismissible. Every number below it is fabricated,
            and a reader who misses that will misread all of them. */}
        <div className="border-t border-status-warn/30 bg-status-warnSoft">
          <div className="mx-auto max-w-[1500px] px-6 py-2 text-xs text-status-warnInk">
            <strong className="font-semibold">Demo data — synthetically generated.</strong>{' '}
            Parts, prices and shortages are produced by a seeded generator and do not
            correspond to real components or real market data. See{' '}
            <code className="font-mono">docs/data-sources.md</code>.
          </div>
        </div>
      </header>

      <main className="mx-auto w-full max-w-[1500px] flex-1 px-6 py-6">
        <div className="grid gap-6 lg:grid-cols-[35fr_65fr]">
          <div className="rounded-lg border border-hairline bg-surface p-4">
            <RfqInput
              value={text}
              onChange={(next) => {
                setText(next);
                if (next.trim()) setUploadedFile(null);
              }}
              onAnalyze={handleAnalyze}
              onAnalyzeFile={handleFile}
              onCancel={handleCancel}
              loading={loading}
              uploadedFile={uploadedFile}
              onClearUpload={() => setUploadedFile(null)}
            />
          </div>

          <div className="min-h-[32rem] lg:h-[calc(100vh-14rem)]">
            <ResultsPanel
              result={result}
              loading={loading}
              error={error}
              onAcceptSuggestion={handleAcceptSuggestion}
              resolvingIndex={resolvingIndex}
              onRetry={handleAnalyze}
            />
          </div>
        </div>
      </main>

      <footer className="border-t border-hairline bg-surface">
        <div className="mx-auto max-w-[1500px] px-6 py-3 text-xs text-ink-muted">
          Market heat compares a part's quote dispersion against its own 52-week baseline.
          Forecasts are next-week medians; see docs/backtest-results.md for where each model
          wins.
        </div>
      </footer>
    </div>
  );
}
