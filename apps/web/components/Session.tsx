import Link from 'next/link';
import type {Payload} from '@/lib/data';
import { NewsThumbnail } from './NewsThumbnail';

export function EvidenceNotice({payload}: {payload:Payload}) {
  const collection=payload.events?.collection;
  const dartStatus=payload.events?.dart_collection?.status;
  const evidence=payload.evidence_assessment;
  const differences=Object.entries(payload.cross_source_checks || {}).filter(([,check])=>check.status==='different');
  const reused=Object.entries(payload.indexes || payload.market_signals || {}).filter(([,quote])=>quote.source_warning);
  return <>{evidence && <p className={evidence.verified ? 'muted' : 'notice'}><strong>근거 상태 · {evidence.status}</strong> — {evidence.guidance}</p>}
    {collection && <p className="muted">뉴스 후보 {collection.candidate_count}건 · 중복 통합 후 {collection.distinct_events}건 · 선정 {collection.selected}건 중 본문 {collection.body_count}건 확보</p>}
    {dartStatus && ['failed','not_configured'].includes(dartStatus) && <p className="notice">공시 {dartStatus==='failed'?'수집 실패':'수집 미설정'} · 공시가 없다는 의미가 아닙니다.</p>}
    {payload.generation && payload.generation.mode!=='ai' && <p className="notice">시장 요약 작성 상태 · {payload.generation.reason}</p>}
    {differences.length>0 && <p className="notice" role="alert">출처 간 가격 차이: {differences.map(([key])=>key.toUpperCase()).join(', ')}. 수집시각·원문 확인 전 해당 지표 해석을 보류하세요.</p>}
    {reused.map(([key,quote])=><p className="notice" key={key}>{key.toUpperCase()} · {quote.source_warning} 기준 {quote.as_of}</p>)}
  </>;
}

export function DailySession({payload}: {payload:Payload}) {
  const session=payload.daily_session;
  if (!session) return null;
  return <section aria-label="세션별 시장 준비">
    <div className="section-heading"><span>DAILY DESK</span><h2>{session.title}</h2></div>
    <h3>{session.session==='morning' ? '미국장 관측 결과' : '국내장 관측 결과'}</h3>
    {session.facts.map((fact,i)=><p key={i}>{fact}</p>)}
    {session.issues && <section className="daily-issues" aria-label="핵심 이슈 브리핑">
      <div className="section-heading"><span>NEWS BRIEFING</span><h2>{session.session==='morning' ? '미국 기술주 아침 브리핑 · 국내장 연결' : '마감 이슈와 오늘 밤 미국장'}</h2></div>
      <p className="muted">사건 → 주가 관측 → 국내 영향 → 다음 확인 순서로 읽으세요. 주가와 뉴스가 함께 나타났다는 사실만으로 원인을 확정하지 않습니다.</p>
      {session.coverage && <p className="meta">주요 이슈 {session.coverage.selected}건 · 미국 관련 {session.coverage.us}건 · AI 분석 {session.coverage.analyzed}건</p>}
      {session.issues.length===0 && <p className="notice">해당 관측 구간에 확인한 이슈가 없습니다.</p>}
      {session.coverage?.us===0 && <p className="notice">이번 발행에서는 미국 관련 기사 근거를 확보하지 못했습니다.</p>}
      {session.issues.map((issue,index)=><article className="daily-issue" key={issue.id}>
        <p className="eyebrow">ISSUE {String(index+1).padStart(2,'0')} · {issue.region}</p>
        <div className="event-heading"><h3>{issue.title}</h3><NewsThumbnail src={issue.image_url} href={/^https:\/\//.test(issue.url) ? issue.url : undefined} title={issue.title}/></div>
        <p className="meta">{issue.source} · {issue.published_at || '기사 시각 미제공'} · {issue.analysis_note}</p>
        <dl className="issue-facts"><div><dt>무슨 일이 있었나</dt><dd>{issue.fact}</dd></div>
          <div><dt>주가 반응 확인</dt><dd>{issue.observations.length ? <>{issue.observations.map(quote=><p key={quote.ticker}><strong>{quote.ticker}</strong> {quote.price} ({quote.change}) · {quote.market_status} · {quote.as_of || '기준시각 미제공'}</p>)}<small>수집 시점의 최근 정규장 관측값입니다. 기사 발표 이후 반응인지 별도 대조가 필요하며 시간외 가격은 포함하지 않습니다.</small></> : '관련 개별 종목의 주가 반응은 확인하지 못했습니다.'}</dd></div>
          <div><dt>국내 투자자에게 미치는 영향</dt><dd><span className="muted">조건부 해석 · </span>{issue.korea_impact}</dd></div>
          <div><dt>{session.session==='morning' ? '국내 개장 후 확인할 것' : '오늘 밤 미국장에서 확인할 것'}</dt><dd>{issue.next_check}</dd></div></dl>
        <details><summary>이 해석을 다시 봐야 하는 조건</summary><p>{issue.invalidation}</p></details>
        {/^https:\/\//.test(issue.url) && <a href={issue.url} target="_blank" rel="noopener noreferrer">기사 원문 확인 →</a>}
      </article>)}
    </section>}
    {session.review.length>0 && <details><summary>아침 관점과 실제 국내장 비교</summary>
      {session.review.map((line,i)=><p key={i}>{line}</p>)}
      {session.morning_report_id && <Link href={`/daily?id=${encodeURIComponent(session.morning_report_id)}`}>아침 보고서 원문 →</Link>}
    </details>}
    <div className="scenario"><h3>{session.session==='morning' ? '국내 개장 후 확인할 조건' : '미국 개장 후 확인할 조건'}</h3>
      <p><b>상방 확인</b> · {session.upside}</p><p><b>하방 확인</b> · {session.downside}</p>
    </div><p className="muted">{session.limits}</p>
  </section>;
}
