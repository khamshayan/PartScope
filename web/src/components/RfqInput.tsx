import { useRef, useState } from 'react';

import { SAMPLE_RFQ } from '../sampleRfq';

interface Props {
  value: string;
  onChange: (value: string) => void;
  onAnalyze: () => void;
  onAnalyzeFile: (file: File) => void;
  onCancel: () => void;
  loading: boolean;
  uploadedFile: File | null;
  onClearUpload: () => void;
}

export function RfqInput({
  value,
  onChange,
  onAnalyze,
  onAnalyzeFile,
  onCancel,
  loading,
  uploadedFile,
  onClearUpload,
}: Props) {
  const [dragging, setDragging] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);

  const lineCount = value.trim() ? value.trim().split(/\r?\n/).length : 0;
  const hasInput = lineCount > 0 || uploadedFile !== null;

  const handleDrop = (event: React.DragEvent) => {
    event.preventDefault();
    setDragging(false);
    const file = event.dataTransfer.files?.[0];
    if (file) onAnalyzeFile(file);
  };

  return (
    <div className="flex h-full flex-col gap-4">
      <div className="flex items-baseline justify-between">
        <label htmlFor="rfq-text" className="text-sm font-medium text-ink">
          Paste RFQ — one part per line
        </label>
        <button
          type="button"
          onClick={() => onChange(SAMPLE_RFQ)}
          disabled={loading}
          className="text-xs font-medium text-accent hover:text-accent-hover disabled:text-ink-muted"
        >
          Load example
        </button>
      </div>

      <textarea
        id="rfq-text"
        value={value}
        onChange={(event) => onChange(event.target.value)}
        // Ctrl/Cmd+Enter submits: this is a paste-and-go tool, and reaching for
        // the mouse after every paste gets old within about four RFQs.
        onKeyDown={(event) => {
          if ((event.metaKey || event.ctrlKey) && event.key === 'Enter') onAnalyze();
        }}
        spellCheck={false}
        placeholder={'STM32F103C8T6 x 500\n296-LM317T-ND\nCRCW060310K0FKEA  2,000 pcs\n\nOr paste a whole email — signatures and reply chains are ignored.'}
        className="min-h-[18rem] flex-1 resize-none rounded-md border border-hairline bg-surface p-3 font-mono text-[13px] leading-relaxed text-ink placeholder:text-ink-muted focus:border-accent focus:outline-none focus:ring-2 focus:ring-accent-ring"
      />

      <div
        onDragOver={(event) => {
          event.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={handleDrop}
        onClick={() => fileInput.current?.click()}
        role="button"
        tabIndex={0}
        onKeyDown={(event) => {
          if (event.key === 'Enter' || event.key === ' ') fileInput.current?.click();
        }}
        className={`cursor-pointer rounded-md border border-dashed p-4 text-center text-xs transition-colors ${
          dragging
            ? 'border-accent bg-accent-soft text-accent-hover'
            : 'border-hairline bg-page text-ink-secondary hover:border-accent/50'
        }`}
      >
        <input
          ref={fileInput}
          type="file"
          accept=".xlsx,.xlsm,.txt,.csv,.eml,.md,text/plain"
          className="hidden"
          onChange={(event) => {
            const file = event.target.files?.[0];
            if (file) onAnalyzeFile(file);
            event.target.value = '';
          }}
        />
        <span className="font-medium">Drop a BOM or an email</span> or click to browse
        <div className="mt-1 text-ink-muted">
          .xlsx, .txt, .csv, .eml — columns are inferred from cell content, so the
          headers need not match anything
        </div>
      </div>

      {/* A spreadsheet has no readable text to show in the textarea, so the
          upload is represented as a chip instead of dumping its bytes there. */}
      {uploadedFile && lineCount === 0 && (
        <div className="flex items-center justify-between gap-2 rounded-md border border-accent-ring bg-accent-soft px-3 py-2 text-xs">
          <span className="truncate text-ink">
            <strong className="font-medium">{uploadedFile.name}</strong>
            <span className="ml-2 text-ink-secondary">
              {(uploadedFile.size / 1024).toFixed(0)} KB · parsed server-side
            </span>
          </span>
          <button
            type="button"
            onClick={onClearUpload}
            className="shrink-0 text-ink-muted hover:text-ink"
            aria-label="Remove uploaded file"
          >
            Clear
          </button>
        </div>
      )}

      <div className="flex items-center gap-3">
        <button
          type="button"
          onClick={loading ? onCancel : onAnalyze}
          disabled={!loading && !hasInput}
          className={`rounded-md px-4 py-2 text-sm font-medium text-white transition-colors ${
            loading
              ? 'bg-ink-secondary hover:bg-ink'
              : 'bg-accent hover:bg-accent-hover disabled:bg-hairline disabled:text-ink-muted'
          }`}
        >
          {loading ? 'Cancel' : 'Analyze'}
        </button>
        <span className="text-xs text-ink-muted">
          {lineCount > 0
            ? `${lineCount} line${lineCount === 1 ? '' : 's'}`
            : uploadedFile
              ? 'file ready'
              : 'nothing to analyse'}
          {!loading && hasInput && <span className="ml-2">· Ctrl+Enter</span>}
        </span>
      </div>
    </div>
  );
}
