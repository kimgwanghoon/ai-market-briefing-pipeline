import Link from 'next/link';
import { getBriefings, labels } from '@/lib/data';
import { Empty, ReportList } from '@/components/Desk';
import Refresh from '@/components/Refresh';
export const dynamic = 'force-dynamic';
export default async function Home() {
  const [live,daily,weekly] = await Promise.all(['live','daily','weekly'].map(kind => getBriefings({kind},1)));
  const rows = [live,daily,weekly].flatMap(r => r.rows).sort((a,b) => b.generated_at.localeCompare(a.generated_at));
  const current = rows[0];
  const summary = current?.payload.key_points || current?.payload.summary_items?.filter(s=>!s.startsWith('[')) || [];
  return <><div className="page-top"><span className="eyebrow">INDEPENDENT MARKET RESEARCH</span><Refresh/></div><section className="home-hero"><p className="eyebrow">MARKET PERSPECTIVE</p><h1>{current?.title || <>흐름을 읽고,<br/>판단의 근거를 찾다.</>}</h1><p>{summary[0]?.replace(/\*\*/g,'') || '시장 데이터와 주요 이벤트를 연결하는 리서치 데스크. 오늘의 변화부터 다음 주의 조건까지 살펴봅니다.'}</p>{current && <Link href={`/${current.kind}?id=${current.id}`}>분석과 근거 읽기 →</Link>}</section><div className="section-heading"><span>LATEST RESEARCH</span><h2>오늘의 리서치</h2></div>{rows.length ? <ReportList rows={rows}/> : <Empty state={[live,daily,weekly].some(r => r.state === 'error') ? 'error' : live.state}/>}{rows.length > 0 && [live,daily,weekly].some(r=>r.state==='error') && <p className="notice">일부 브리핑을 조회하지 못했습니다. 잠시 후 다시 확인해 주세요.</p>}<div className="edition-grid">{Object.entries(labels).map(([kind,label],i) => <Link key={kind} href={`/${kind}`}><span className="edition-number">0{i+1}</span><h2>{label}</h2><p>{kind === 'live' ? '지금 달라진 시장의 흐름' : kind === 'daily' ? '하루의 시장 관점과 관심 업종·종목' : '한 주의 흐름과 다음 주 시나리오'}</p><span>리서치 읽기 →</span></Link>)}</div></>;
}
