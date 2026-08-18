import { useCallback, useEffect, useRef, useState } from 'react';

import {
  ApiFailure,
  analyzeFile,
  analyzeText,
  fetchPart,
  fetchSession,
  logout,
} from './api/client';
import type { LineItem, RfqResponse, Session } from './api/types';
import { LoginForm } from './components/LoginForm';
import { ResultsPanel } from './components/ResultsPanel';
import { RfqInput } from './components/RfqInput';

export default function App() {
  // undefined = the check has not come back yet, null = signed out. The three
  // states are distinct: rendering the login form while the check is still in
  // flight would flash it at someone who is already signed in.
  const [session, setSession] = useState<Session | null | undefined>(undefined);
  const [text, setText] = useState('');
  const [result, setResult] = useState<RfqResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<ApiFailure | null>(null);
  const [resolvingIndex, setResolvingIndex] = useState<number | null>(null);
  const [uploadedFile, setUploadedFile] = useState<File | null>(null);

  // Lets a slow analyse be cancelled, and stops an abandoned request from
  // landing on top of a newer one.
  const inFlight = useRef<AbortController | null>(null);

  useEffect(() => {
    let cancelled = false;
    void fetchSession()
      .then((found) => {
        if (!cancelled) setSession(found);
      })
      .catch(() => {
        if (!cancelled) setSession(null);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const handleLogout = useCallback(async () => {
    inFlight.current?.abort();
    await logout();
    // Everything the previous session loaded goes with it. Leaving an analysed
    // RFQ on screen behind a login form would be the one thing signing out is
    // supposed to prevent.
    setSession(null);
    setResult(null);
    setText('');
    setUploadedFile(null);
    setError(null);
  }, []);

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
      // An expired session mid-session: send them back to the login form
      // rather than showing "Sign in to use this endpoint" as an analysis
      // error they cannot act on from here.
      if (caught instanceof ApiFailure && caught.status === 401) {
        setSession(null);
        setResult(null);
        return;
      }
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

  // Nothing until the session check answers. It is one same-origin request, so
  // this is a frame or two -- a spinner here would flash more than it informs.
  if (session === undefined) {
    return <div className="min-h-screen bg-page" />;
  }

  if (session === null) {
    return <LoginForm onSignedIn={setSession} />;
  }

  return (
    <div className="flex min-h-screen flex-col bg-page text-ink">
      <header className="border-b border-hairline bg-surface">
        <div className="mx-auto max-w-[1500px] px-6 py-4">
          <div className="flex flex-wrap items-baseline justify-between gap-2">
            <div>
              <h1 className="text-lg font-semibold tracking-tight text-ink">PartScope</h1>
              <p className="text-sm text-ink-secondary">
                RFQ triage and price-band recommendation
              </p>
            </div>
            <div className="flex items-baseline gap-4">
              <a
                className="text-xs text-ink-muted hover:text-accent"
                href="https://github.com"
                onClick={(event) => event.preventDefault()}
              >
                secondary-market component sourcing
              </a>
              {/* Only when there is a session to end. On a server with no
                  credentials configured there is nothing to log out of, and
                  offering it would imply a protection that is not there. */}
              {session.auth_required && (
                <button
                  type="button"
                  onClick={() => void handleLogout()}
                  className="text-xs text-ink-muted hover:text-accent"
                >
                  {session.username ? `${session.username} · ` : ''}Log out
                </button>
              )}
            </div>
          </div>
        </div>

        {/* Persistent, not dismissible. Every number below it is fabricated,
            and a reader who misses that will misread all of them. */}
        <div className="border-t border-status-warn/30 bg-status-warnSoft">
          <div className="mx-auto max-w-[1500px] px-6 py-2 text-xs text-status-warnInk">
            <strong className="font-semibold">Real parts, synthetic pricing.</strong>{' '}
            The catalog is real component data from Mouser&apos;s distributor API. Every
            price, forecast and market-heat figure is produced by a seeded generator and
            does not correspond to real quotes or real market activity. See{' '}
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
