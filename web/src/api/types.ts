/**
 * Shapes returned by the Express API.
 *
 * Every pricing field is nullable on purpose. A line that matched nothing, or a
 * part nobody has quoted, comes back with nulls rather than zeros -- a $0.0000
 * price and a green STABLE chip would be a confident claim that happens to be
 * false. The UI has to render absence as absence.
 */

export type VolatilityFlag = 'STABLE' | 'ELEVATED' | 'VOLATILE';

export type MatchMethod =
  | 'exact'
  | 'confusable_variant'
  | 'prefix'
  | 'base_part'
  | 'fuzzy'
  | 'no_match';

export interface Alternate {
  mpn: string;
  manufacturer: string;
  description: string;
  lifecycle_status: string;
  authorized_stock: number;
  similarity: number;
}

export interface NearMiss {
  mpn: string;
  manufacturer: string;
  description: string;
  confidence: number;
}

export interface CatalogPart {
  mpn: string;
  manufacturer: string;
  category: string;
  description: string;
  package: string;
  lifecycle_status: string;
  is_defense_grade: boolean;
  authorized_stock: number;
  datasheet_specs: Record<string, unknown>;
  date_introduced: string;
}

export interface LineItem {
  input_string: string;
  quantity: number | null;

  // matching
  matched_mpn: string | null;
  manufacturer: string | null;
  description: string | null;
  category: string | null;
  lifecycle_status: string | null;
  authorized_stock: number | null;
  is_defense_grade: boolean | null;
  date_introduced: string | null;
  confidence: number;
  match_method: MatchMethod;
  normalized_key: string;
  rules_applied: string[];
  alternates: Alternate[];
  near_misses: NearMiss[];

  // pricing — null means "no data", never zero
  price_p25: number | null;
  price_p50: number | null;
  price_p75: number | null;
  quote_count: number;
  weeks_of_history: number;
  forecast_price: number | null;
  forecast_model: string | null;
  heat_index: number | null;
  volatility_flag: VolatilityFlag | null;
  sparkline: number[];

  catalog?: CatalogPart | null;
}

export interface RfqResponse {
  rfq_id: number;
  created_at: string;
  source_type: string;
  source_name: string | null;
  count: number;
  skipped_lines: string[];
  model: string;
  elapsed_ms: number;
  items: LineItem[];
}

export interface ApiErrorBody {
  error: {
    code: string;
    message: string;
    details?: unknown;
  };
}

export interface PartDetail extends Partial<LineItem> {
  mpn: string;
  history?: Array<{
    week_start: string;
    p25: number;
    p50: number;
    p75: number;
    quotes: number;
    lead_time_days: number;
    dispersion_ratio: number;
  }>;
  catalog?: CatalogPart | null;
}
