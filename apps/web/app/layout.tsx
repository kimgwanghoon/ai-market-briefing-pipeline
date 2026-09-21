import type { Metadata } from 'next';
import Link from 'next/link';
import '@fontsource/noto-sans-kr/400.css';
import '@fontsource/noto-sans-kr/700.css';
import './globals.css';
export const preferredRegion = 'icn1';
export const metadata: Metadata = { title: 'Market Journal | 리서치 데스크', description: '근거로 읽는 시장. 장중 브리핑, 데일리 리서치, 주간 전략.' };
export default function Layout({ children }: { children: React.ReactNode }) {
  return <html lang="ko"><body><a className="skip" href="#content">본문 바로가기</a>
    <header className="masthead"><div className="brand-row"><Link className="brand" href="/">MARKET JOURNAL<span>RESEARCH DESK</span></Link><p>시장의 흐름을 읽는<br/>오늘의 관점</p></div>
      <nav aria-label="주요 메뉴">{[['/','시장 개요'],['/live','장중 브리핑'],['/daily','데일리 리서치'],['/weekly','주간 전략'],['/archive','자료실']].map(([url,label]) => <Link key={url} href={url}>{label}</Link>)}</nav>
    </header><main id="content">{children}</main><footer><strong>MARKET JOURNAL</strong><p>AI 기반 시장 분석 · 관측 사실과 해석을 구분합니다.<br/>관심 종목은 근거 확인을 위한 관찰 목록이며 투자수익을 보장하지 않습니다.</p></footer></body></html>;
}
