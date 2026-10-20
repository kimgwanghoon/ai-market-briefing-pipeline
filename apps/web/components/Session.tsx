import Link from 'next/link';
import type {Payload} from '@/lib/data';

export function EvidenceNotice({payload}: {payload:Payload}) {
  const evidence=payload.evidence_assessment;
  const differences=Object.entries(payload.cross_source_checks || {}).filter(([,check])=>check.status==='different');
  return <>{evidence && <p className={evidence.verified ? 'muted' : 'notice'}><strong>근거 상태 · {evidence.status}</strong> — {evidence.guidance}</p>}
    {payload.generation?.mode==='rules' && <p className="notice">작성 상태 · {payload.generation.reason}</p>}
    {differences.length>0 && <p className="notice" role="alert">출처 간 가격 차이: {differences.map(([key])=>key.toUpperCase()).join(', ')}. 수집시각·원문 확인 전 해당 지표 해석을 보류하세요.</p>}
  </>;
}

export function DailySession({payload}: {payload:Payload}) {
  const session=payload.daily_session;
  if (!session) return null;
  return <section aria-label="세션별 시장 준비">
    <div className="section-heading"><span>DAILY DESK</span><h2>{session.title}</h2></div>
    <h3>{session.session==='morning' ? '미국장 관측 결과' : '국내장 관측 결과'}</h3>
    {session.facts.map((fact,i)=><p key={i}>{fact}</p>)}
    {session.review.length>0 && <details><summary>아침 관점과 실제 국내장 비교</summary>
      {session.review.map((line,i)=><p key={i}>{line}</p>)}
      {session.morning_report_id && <Link href={`/daily?id=${encodeURIComponent(session.morning_report_id)}`}>아침 보고서 원문 →</Link>}
    </details>}
    <div className="scenario"><h3>{session.session==='morning' ? '국내 개장 후 확인할 조건' : '미국 개장 후 확인할 조건'}</h3>
      <p><b>상방 확인</b> · {session.upside}</p><p><b>하방 확인</b> · {session.downside}</p>
    </div><p className="muted">{session.limits}</p>
  </section>;
}
