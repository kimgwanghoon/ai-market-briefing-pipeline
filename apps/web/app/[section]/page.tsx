import Link from 'next/link';
import { notFound } from 'next/navigation';
import { getBriefings, Kind, labels, Query } from '@/lib/data';
import { Empty, Report, ReportList } from '@/components/Desk';
import Refresh from '@/components/Refresh';
export const dynamic = 'force-dynamic';
export default async function Section({ params, searchParams }: { params: Promise<{section:string}>; searchParams: Promise<Record<string,string|string[]|undefined>> }) {
  const { section } = await params;
  if (!['live','daily','weekly','archive'].includes(section)) notFound();
  const raw = await searchParams;
  const query: Query = Object.fromEntries(Object.entries(raw).filter(([,v]) => typeof v === 'string'));
  const archive = section === 'archive';
  const result = await getBriefings({...query, kind: archive ? query.kind : section}, archive ? 12 : 1);
  const page = Math.max(0, Math.min(10000, Math.floor(Number(query.page)||0)));
  const next = (p: number) => { const s = new URLSearchParams(); for (const [k,v] of Object.entries(query)) if(v) s.set(k,v); s.set('page',String(p)); return `/${section}?${s}`; };
  return <><div className="page-top"><span className="eyebrow">{archive ? 'RESEARCH ARCHIVE' : labels[section as Kind]}</span><Refresh auto={section === 'live' && !query.id && !query.date}/></div>{archive && <><h1>리서치 자료실</h1><p className="muted">날짜와 주제로 지난 시장의 판단을 확인합니다.</p></>}<form className="filters" action={`/${section}`}><label>발행일<input type="date" name="date" defaultValue={query.date}/></label>{archive && <><label>종류<select name="kind" defaultValue={query.kind || ''}><option value="">전체</option>{Object.entries(labels).map(([k,v])=><option value={k} key={k}>{v}</option>)}</select></label><label className="search-field">주제·업종·종목<input type="search" name="q" maxLength={80} defaultValue={query.q} placeholder="검색어를 입력하세요"/></label></>}<button type="submit">조회</button><Link href={`/${section}`}>초기화</Link></form>{!result.rows.length ? <Empty state={result.state}/> : archive ? <ReportList rows={result.rows}/> : <Report row={result.rows[0]} historical={!!query.id || !!query.date || page > 0}/>}{!query.id && <div className="pagination">{page > 0 && <Link href={next(page-1)}>← 더 최근 자료</Link>}{result.hasMore && <Link href={next(page+1)}>이전 자료 →</Link>}</div>}</>;
}
