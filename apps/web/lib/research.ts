import type { Briefing, Market, Payload } from './data';

export const marketNames: Record<string,string> = {kospi:'KOSPI',kosdaq:'KOSDAQ',sp500:'S&P 500',nasdaq:'NASDAQ',dow:'DOW',ewy:'EWY',vix:'VIX',usdkrw:'USD/KRW',us10y:'미10년물',wti:'WTI'};
export const components: Record<string,string> = {market:'시장',news:'뉴스',dart:'공시',sector:'업종'};
export const marketsOf = (p: Payload) => p.market_signals || p.indexes || {};
export const number = (value: string) => value && value !== 'N/A' && Number.isFinite(Number(value.replaceAll(',',''))) ? Number(value.replaceAll(',','')) : null;
export const signed = (value: number) => `${value > 0 ? '+' : ''}${value.toFixed(2)}`;

export function indicator(p: Payload) {
  const s = p.sentiment;
  if (!s?.normalized_components || !s.weights) return null;
  const parts = Object.entries(components).map(([key,label]) => {
    const n = s.normalized_components![key], w = s.weights![key];
    return {key,label,weight:w,value:n * w * 25 / 1.5};
  });
  if (!parts.every(x=>Number.isFinite(x.value))) return null;
  const beforeClamp = 50 + parts.reduce((sum,x)=>sum+x.value,0);
  const clamped = Math.min(100,Math.max(0,beforeClamp));
  // Old snapshots round normalized inputs: disclose the small rounding difference.
  if (Math.abs(clamped - s.score) > .15) return null;
  return {parts, clamp:clamped-beforeClamp, rounding:s.score-clamped};
}

export function observationAge(m: Market, generatedAt: string) {
  if (!m.source_timestamp) return '원본 기준시각 미기록';
  const age = (Date.parse(generatedAt) - Date.parse(m.source_timestamp)) / 60000;
  if (!Number.isFinite(age) || age < 0) return '기준시각 검증 필요';
  return `발행 시 ${age >= 60 ? `${Math.floor(age/60)}시간 ${Math.floor(age%60)}분` : `${Math.floor(age)}분`} 전 관측`;
}

export function conflicts(current: Briefing, other?: Briefing) {
  if (!other) return [];
  const old = marketsOf(other.payload);
  return Object.entries(marketsOf(current.payload)).filter(([key,m])=> {
    const p = old[key];
    if (!p || number(m.price) === null || number(p.price) === null) return false;
    const stamp = m.source_timestamp || m.as_of;
    const sameTime = stamp && stamp !== '기준시각 확인 필요' && stamp === (p.source_timestamp || p.as_of);
    const compatible = (!m.price_basis || !p.price_basis || m.price_basis === p.price_basis);
    return sameTime && compatible && m.price !== p.price;
  }).map(([key])=>marketNames[key] || key);
}
