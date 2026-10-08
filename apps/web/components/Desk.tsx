import Link from 'next/link';
import { Research } from './Research';
import { Briefing, Event, labels, Payload, Result, safeUrl, time } from '@/lib/data';

export function Empty({ state }: { state: Result['state'] }) {
  const message = state === 'error' ? '자료를 불러오지 못했습니다' : state === 'unconfigured' ? '리서치 서비스 준비 중' : '아직 발행된 자료가 없습니다';
  return <div className="empty"><span className="eyebrow">RESEARCH DESK</span><h2>{message}</h2><p>{state === 'error' ? '잠시 후 다시 확인해 주세요. 기존 자료가 삭제된 것은 아닙니다.' : '첫 브리핑 발행 후 시황과 분석이 표시됩니다.'}</p></div>;
}

function plain(value?: string) { return value?.replace(/\*\*/g, '').replace(/^\[.*?\]\s*/, '').trim() || ''; }

function takeaway(row: Briefing) {
  const payload = row.payload;
  return plain(payload.headline) || plain(payload.key_points?.find(item => !/^\[.*\]$/.test(item))) || plain(payload.summary_items?.find(item => !/^\[.*\]$/.test(item))) || payload.watchpoint || '핵심 내용을 정리하고 있습니다.';
}

export function ReportList({ rows }: { rows: Briefing[] }) {
  return <div className="report-list">{rows.map(row => <Link className="report-link" href={`/${row.kind}?id=${row.id}`} key={row.id}><span className="eyebrow">{labels[row.kind]}</span><h3>{row.title}</h3><p>{takeaway(row)}</p><span className="muted">{time(row.generated_at)}</span><span className="arrow">↗</span></Link>)}</div>;
}

export function Events({ items }: { items: Event[] }) {
  if (!items.length) return <p className="muted">새로 확인된 이벤트가 없습니다.</p>;
  return <div className="events">{items.map((event, index) => {
    const url = safeUrl(event.url || event.link);
    return <article key={`${event.title}-${index}`}><span className="rank">{String(index + 1).padStart(2, '0')}</span><div><h3>{url ? <a href={url} target="_blank" rel="noopener noreferrer">{event.corp_name} {event.title} ↗</a> : event.title}</h3>{event.research_insight ? <><span className="tag">{event.research_insight.impact}</span><p><b>무슨 일인가요</b> {event.research_insight.fact}</p><p><b>시장에 미칠 수 있는 영향</b> {event.research_insight.interpretation}</p><p><b>다음 확인</b> {event.research_insight.watchpoint}</p><small>{event.research_insight.basis}</small></> : event.why_it_matters ? <p>{event.why_it_matters}</p> : <p className="muted">원문 확인 전 영향 판단 보류</p>}<small>{event.source || event.press || '원문 참고'} · {event.published_at || event.time || '발행시각 미제공'}</small></div></article>;
  })}</div>;
}

function EventGroup({ label, items, collected }: { label: string; items: Event[]; collected?: number }) {
  const first = items.slice(0, 2), rest = items.slice(2);
  return <div className="event-group"><div className="event-group-title"><h3>{label}</h3><span>{collected ?? items.length}건 확인 · 주요 {items.length}건</span></div><Events items={first}/>{rest.length > 0 && <details className="event-more"><summary>{label} {rest.length}건 더보기</summary><Events items={rest}/></details>}</div>;
}

function Text({ value }: { value: string }) { return <>{value.split(/(\*\*.*?\*\*)/g).map((part, index) => part.startsWith('**') ? <strong key={index}>{part.slice(2, -2)}</strong> : part)}</>; }

function ReaderSummary({ row, points }: { row: Briefing; points: string[] }) {
  const payload = row.payload;
  const outlook = payload.sentiment?.label || payload.next_week_outlook?.bias || '근거를 확인하며 관망';
  const summary = takeaway(row);
  const next = payload.watchpoint || payload.summary?.top_watchpoint || '주요 지표와 후속 뉴스가 현재 흐름을 이어가는지 확인하세요.';
  return <section className="reader-summary" aria-label="한눈에 보는 브리핑"><div className="reader-summary-heading"><span className="eyebrow">START HERE</span><h2>한눈에 보는 {labels[row.kind]}</h2><p>숫자와 전문 용어보다, 지금 시장이 어떤 상황인지부터 읽어보세요.</p></div><div className="quick-grid"><article><span>지금 분위기</span><strong>{outlook}</strong><p>{payload.sentiment?.interpretation || '시장 신호와 주요 이벤트를 함께 반영한 관점입니다.'}</p></article><article><span>한 줄 요약</span><strong>{summary}</strong></article><article><span>지금 확인할 것</span><strong>{plain(next)}</strong></article></div>{points.length > 0 && <div className="quick-reasons"><h3>왜 이렇게 보나요?</h3><ol>{points.filter(point => !/^\[.*\]$/.test(point)).slice(0, 3).map((point, index) => <li key={index}><Text value={point}/></li>)}</ol></div>}</section>;
}

function DailySections({ payload }: { payload: Payload }) {
  const research = payload.research;
  return <><section><div className="section-heading"><span>02 / SECTOR FOCUS</span><h2>주목 업종</h2></div><p className="muted">뉴스·이벤트 기준 관찰입니다. 업종 수익률 순위는 아닙니다.</p><div className="sector-grid">{research?.sectors?.length ? research.sectors.map(sector => <article key={sector.name}><span className="tag">{sector.stance}</span><h3>{sector.name}</h3><Events items={sector.evidence.slice(0, 1)}/></article>) : <p>업종별 근거를 확보 중입니다.</p>}</div></section><section><div className="section-heading"><span>03 / STOCK WATCH</span><h2>관심 종목</h2></div>{research?.screening && <p className="muted">후보 {research.screening.universe} · 긍정 근거 {research.screening.evidence} · 가격 조회 {research.screening.quote_requested} · 조건 통과 {research.screening.qualified} · 최종 {research.stocks.length}</p>}<p className="muted">{research?.method || '자료가 충분한 종목만 선별합니다.'}</p>{research?.stocks?.length ? research.stocks.map(stock => <article className="stock" key={stock.ticker}><div className="stock-header"><div><span className="eyebrow">{stock.sector} · {stock.status}</span><h3>{stock.name} <small>{stock.ticker}</small></h3></div><div><strong>{stock.quote.price.toLocaleString('ko-KR')}원</strong><small>{time(stock.quote.as_of)}</small></div></div><p><b>선정 근거</b> {stock.reason}</p>{stock.catalyst && <p><b>확인할 계기</b> {stock.catalyst}</p>}<p><b>주요 위험</b> {stock.risk}</p><p><b>재검토 조건</b> {stock.invalidation}</p><details><summary>원문 근거 확인</summary><Events items={stock.evidence}/></details></article>) : <div className="quiet-box">{research?.empty_reason || '선정 기준을 충족하는 자료가 없습니다.'}</div>}</section></>;
}

function WeeklySections({ payload }: { payload: Payload }) {
  return <><section><h2>이번 주 시장 변화</h2><div className="table-wrap"><table><thead><tr><th>지표</th><th>첫 관측</th><th>마지막 관측</th><th>변화</th></tr></thead><tbody>{payload.market_performance?.map(item => <tr key={item.label}><td>{item.label}</td><td>{item.start}</td><td>{item.end}</td><td>{item.change_text}</td></tr>)}</tbody></table></div></section><section><h2>시장 온도 추이</h2><p className="muted">{payload.summary?.trading_days || 0}일 · {payload.summary?.count || 0}개 관측 · 0~100점 보조 지표</p>{payload.daily_points?.map(point => <div className="trend-row" key={point.day}><span>{point.day}</span><meter min="0" max="100" value={point.score_avg} aria-label={`${point.day} 시장 온도`}/><strong>{point.score_avg.toFixed(1)}</strong></div>)}</section><section><h2>이번 주의 기회와 위험</h2><h3>기회 요인</h3><Events items={payload.opportunity_events || []}/><h3>위험 요인</h3><Events items={payload.risk_events || []}/></section><section><h2>다음 주, 이렇게 보면 됩니다</h2><div className="scenario"><h3>기본 관점</h3><p>{payload.next_week_outlook?.bias || '자료 축적 중'}</p><h3>좋아질 조건</h3>{payload.next_week_outlook?.upside_conditions?.map((item, index) => <p key={index}>{item}</p>)}<h3>나빠질 조건</h3>{payload.next_week_outlook?.downside_conditions?.map((item, index) => <p key={index}>{item}</p>)}<p className="muted">예상 범위 {payload.next_week_outlook?.expected_range} · 표본 신뢰도 {payload.next_week_outlook?.confidence}</p></div></section></>;
}

export function Report({ row, previous, peer, comparisonError, historical = false }: { row: Briefing; previous?: Briefing; peer?: Briefing; comparisonError?: boolean; historical?: boolean }) {
  const payload = row.payload, markets = payload.market_signals || payload.indexes || {};
  const names: Record<string, string> = { kospi: 'KOSPI', kosdaq: 'KOSDAQ', sp500: 'S&P 500', nasdaq: 'NASDAQ', dow: 'DOW', ewy: 'EWY', usdkrw: 'USD / KRW', vix: 'VIX', us10y: '미국 10년물', wti: 'WTI' };
  const points = payload.key_points || payload.summary_items || payload.next_week_outlook?.rationale || [];
  const age = Date.now() - new Date(row.generated_at).getTime();
  const old = !historical && age > (row.kind === 'live' ? 2 : row.kind === 'daily' ? 30 : 192) * 3600000;
  return <><section className="hero"><div><span className="eyebrow">{labels[row.kind]} · {payload.edition_title || 'MARKET PERSPECTIVE'}</span><h1>{row.title}</h1><p className="meta">발행 {time(row.generated_at)}{historical && ' · 과거 보고서'}</p>{row.observation_end && <p className="meta">관측 종료 {time(row.observation_end)}</p>}{old && <p className="notice">최근 생성 이후 시간이 경과했습니다. 휴장·실행 일정과 지표별 기준시각을 확인하세요.</p>}</div><aside className="hero-aside"><span className="eyebrow">오늘의 관점</span><strong>{payload.sentiment?.label || payload.next_week_outlook?.bias || '근거 중심의 시장 점검'}</strong><p>{payload.sentiment?.interpretation || '시장 변화와 주요 이벤트를 함께 살펴봅니다.'}</p>{payload.sentiment && <small>시장 온도 {payload.sentiment.score} / 100 · 보조 지표</small>}</aside></section><ReaderSummary row={row} points={points}/>{Object.keys(markets).length > 0 && <section className="market-strip" aria-label="주요 시장 지표">{Object.entries(markets).map(([key, market]) => <div key={key}><span>{names[key] || key}</span><strong>{market.price}</strong><em className={/[▲+]/.test(market.change) ? 'up' : /[▼−-]/.test(market.change) ? 'down' : ''}>{market.change}</em><small>{market.market_status} · {market.as_of || '기준시각 미제공'}</small></div>)}</section>}<div className="reading-layout"><div className="main-column"><section><div className="section-heading"><span>MARKET STORY</span><h2>시장은 왜 이렇게 움직이나요?</h2></div><div className="analysis">{points.length ? points.map((point, index) => /^\[.*\]$/.test(point) ? <h3 key={index}>{point.slice(1, -1)}</h3> : <p key={index}><Text value={point}/></p>) : <p>분석에 필요한 데이터가 축적 중입니다.</p>}</div></section>{row.kind === 'daily' && <DailySections payload={payload}/>} {row.kind === 'weekly' ? <WeeklySections payload={payload}/> : <section><div className="section-heading"><span>MARKET DRIVERS</span><h2>뉴스와 공시, 따로 확인하세요</h2></div><p className="muted">원천별로 최대 5건의 핵심 근거를 보관하고, 먼저 중요한 2건씩 보여드립니다.</p><div className="event-groups"><EventGroup label="뉴스" items={payload.news_items || payload.events?.news || []} collected={payload.events?.news_count}/><EventGroup label="공시 (DART)" items={payload.events?.dart || []} collected={payload.events?.dart_count}/></div></section>}{safeUrl(payload.cover_image) && <section><div className="section-heading"><span>EDITORIAL COMIC</span><h2>그림으로 보는 시장</h2></div>{/* eslint-disable-next-line @next/next/no-img-element */}<img className="comic" src={safeUrl(payload.cover_image)} alt="이번 브리핑의 시장 분위기를 표현한 AI 만화" loading="lazy"/><p className="muted">시장 해석을 돕는 AI 생성 이미지입니다.</p></section>}<details className="analyst-details"><summary>분석 근거·전회 변화·데이터 기준 자세히 보기</summary><Research row={row} previous={previous} peer={peer} comparisonError={comparisonError}/></details></div><aside className="sidebar"><section><span className="eyebrow">CHECK NEXT</span><h2>다음 확인 사항</h2><p><Text value={payload.watchpoint || payload.summary?.top_watchpoint || '후속 뉴스와 시장 지표의 변화가 현재 판단을 뒷받침하는지 확인하세요.'}/></p></section><section><span className="eyebrow">HOW TO READ</span><h3>이 리포트 읽는 법</h3><p>먼저 한눈에 보는 요약을 읽고, 필요한 경우 뉴스·공시와 상세 지표를 확인하세요.</p>{payload.collection_cutoff && <p>수집 마감<br/>{time(payload.collection_cutoff)}</p>}<details><summary>데이터 안내</summary><p>발행시각과 시장 지표의 기준시각은 다를 수 있습니다. AI 분석 및 규칙 기반 계산 결과는 투자 판단을 대신하지 않습니다.</p><p>{payload.reliability?.guidance}</p></details></section><Link href="/archive">이전 보고서 찾아보기 →</Link></aside></div></>;
}
