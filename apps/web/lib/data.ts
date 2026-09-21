export type Kind = 'live' | 'daily' | 'weekly';
export const labels: Record<Kind, string> = { live: '장중 브리핑', daily: '데일리 리서치', weekly: '주간 전략' };
export type Event = { title: string; url?: string; link?: string; source?: string; press?: string; published_at?: string; time?: string; why_it_matters?: string; corp_name?: string };
export type Market = { price: string; change: string; as_of?: string; market_status?: string };
type Stock = { name: string; ticker: string; sector: string; status: string; reason: string; risk: string; invalidation: string; basis: string; quote: { price: number; as_of: string }; evidence: Event[] };
export type Payload = {
  headline?: string; summary_items?: string[]; key_points?: string[]; watchpoint?: string;
  window_start?: string; collection_cutoff?: string; edition_title?: string; cover_image?: string;
  indexes?: Record<string, Market>; market_signals?: Record<string, Market>;
  news_items?: Event[]; events?: { news?: Event[]; dart?: Event[] };
  sentiment?: { score: number; label: string; interpretation?: string; data_completeness?: number };
  comparison?: { delta?: number }; reliability?: { status?: string; guidance?: string };
  research?: { method: string; empty_reason: string; sectors: { name: string; stance: string; basis: string; evidence: Event[] }[]; stocks: Stock[] };
  summary?: { count: number; trading_days: number; top_watchpoint?: string; score_avg?: number };
  daily_points?: { day: string; score_avg: number; count: number }[];
  market_performance?: { label: string; start: string; end: string; change_text: string }[];
  next_week_outlook?: { bias: string; expected_range: string; confidence: string; rationale: string[]; upside_conditions: string[]; downside_conditions: string[] };
};
export type Briefing = { id: string; kind: Kind; title: string; generated_at: string; observation_end?: string; payload: Payload };
export type Query = { kind?: string; date?: string; q?: string; page?: string; id?: string };
export type Result = { rows: Briefing[]; state: 'ready' | 'unconfigured' | 'error'; hasMore: boolean };

export function time(value?: string) {
  if (!value) return '기준시각 미제공';
  const normalized = /^\d{4}-\d{2}-\d{2} \d{2}:/.test(value) ? value.replace(' ', 'T') + '+09:00' : value;
  const dt = new Date(normalized);
  if (Number.isNaN(dt.getTime())) return value;
  return new Intl.DateTimeFormat('ko-KR', { timeZone: 'Asia/Seoul', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false }).format(dt) + ' KST';
}
export function safeUrl(value?: string) { return value && /^https?:\/\//i.test(value) ? value : undefined; }

export async function getBriefings(query: Query = {}, size = 12): Promise<Result> {
  if (query.id && !/^[\da-f]{8}-[\da-f]{4}-[\da-f]{4}-[\da-f]{4}-[\da-f]{12}$/i.test(query.id))
    return { rows: [], state: 'ready', hasMore: false };
  const url = process.env.SUPABASE_URL, key = process.env.SUPABASE_PUBLISHABLE_KEY;
  if (!url || !key) return { rows: [], state: 'unconfigured', hasMore: false };
  // A server route must still use a least-privilege public key, never a batch key.
  if (key.startsWith('sb_secret_')) return { rows: [], state: 'error', hasMore: false };
  try {
    if (key.startsWith('ey')) {
      const claims = JSON.parse(Buffer.from(key.split('.')[1], 'base64url').toString());
      if (claims.role !== 'anon') throw new Error('Expected anon key');
    }
    const params = new URLSearchParams({ select: 'id,kind,title,generated_at,observation_end,payload',
      published: 'eq.true', order: 'generated_at.desc', limit: String(size + 1) });
    if (query.kind && query.kind in labels) params.set('kind', `eq.${query.kind}`);
    if (query.id && /^[\da-f-]{36}$/i.test(query.id)) params.set('id', `eq.${query.id}`);
    if (query.date && /^\d{4}-\d{2}-\d{2}$/.test(query.date)) {
      const start = new Date(query.date + 'T00:00:00+09:00');
      params.append('generated_at', `gte.${start.toISOString()}`);
      params.append('generated_at', `lt.${new Date(start.getTime() + 86400000).toISOString()}`);
    }
    if (query.q) params.set('search_text', `ilike.*${query.q.replace(/[%_*]/g, '').slice(0, 80)}*`);
    const page = Math.max(0, Math.min(10000, Number(query.page) || 0));
    params.set('offset', String(Math.floor(page) * size));
    const headers: Record<string, string> = { apikey: key };
    if (!key.startsWith('sb_publishable_')) headers.Authorization = `Bearer ${key}`;
    const response = await fetch(`${url.replace(/\/$/, '')}/rest/v1/briefings?${params}`, {
      headers, cache: 'no-store', signal: AbortSignal.timeout(10000),
    });
    if (!response.ok) throw new Error('Read failed');
    const rows: Briefing[] = await response.json();
    if (!Array.isArray(rows)) throw new Error('Invalid response');
    return { rows: rows.slice(0, size), state: 'ready', hasMore: rows.length > size };
  } catch { return { rows: [], state: 'error', hasMore: false }; }
}
