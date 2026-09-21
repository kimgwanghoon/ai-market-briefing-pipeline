# Research Desk 전환 설계 및 실행 계획

## 목표와 경계
기존 JSON은 이관하지 않는다. Python 배치 / Supabase / Next.js(Vercel)를 한 저장소에서 운영한다.
기존 main.py, intraday.py, weekly_report.py의 검증된 수집·분석 함수를 재사용하되 새 실행 조합은 pipeline/jobs에 둔다.
전체 함수 이동은 이번 전환과 분리한다. 기존 HTML 경로는 롤백 기간에만 유지한다.

## 실행 순서
1. SQL 마이그레이션·RLS·실행 잠금·원자적 공개 구현
2. DB 기반 라이브·데일리·주간 실행, 이미지 저장, 알림 이력 구현
3. 뉴스 근거와 거래대금 검증을 거친 업종·종목 관찰 목록 구현
4. Next.js 시장 개요 / 라이브 / 데일리 / 주간 / 자료실 구현
5. Python 테스트, 웹 타입 검사·프로덕션 빌드, 임시 PostgreSQL 권한·트랜잭션 검사
6. 승인 후 원격 코드 반영, 사용자 DB 마이그레이션·Secrets 등록, Vercel 배포
7. 첫 실행과 화면 연결 확인 후 STORAGE_BACKEND=supabase로 전환

## 데이터 계약
- pipeline_runs: kind + target_at 고유 키. 실행 토큰과 45분 lease로 동시 실행 차단. 실패 또는 만료 후 재시도.
- briefings: 공개 완료 결과만 저장. 같은 kind + target_at을 수정하지 않는다.
- market_snapshots: 당시 지표 데이터와 원문 기준시각 보존.
- events: 원천 식별자/URL 해시로 공통 원문 중복 제거. 발행시각 원문은 훼손하지 않는다.
- briefing_events: 브리핑 당시 중요도·순서·원문 사본을 보존. 이후 뉴스 변경에도 분석 근거 유지.
- briefings.observations: 분석 입력, analysis: 분석 출력, versions: 모델·프롬프트·점수 버전.
- briefings.payload: 기존 분석 함수와의 호환용 데이터. 프런트엔드도 초기에는 이 계약을 사용한다.
- notifications: 전송 결과는 pipeline_runs에 별도 기록. Discord 응답 유실 시 정확히 한 번 보장은 불가능하므로 unknown 상태를 자동 재전송하지 않는다.
- 모든 DB 시각은 timestamptz. Python 기존 문자열은 KST로 해석 후 변환. 화면은 Asia/Seoul 명시.

## 화면
네이비·화이트·얇은 구분선의 리서치 포털. 실제 금융사/애널리스트를 사칭하지 않는다.
시장 판단 → 지표 → 근거 → 위험·조건 → 상세 순서. 점수는 보조 지표다.
데일리: 시장 관점, 지표, 이벤트상 주목 업종 3개, 관심 종목 최대 3개, 뉴스, 만화.
아카이브는 종류·날짜·검색·페이지 이동. 보고서 ID로 고정 링크를 제공한다.
빈 DB/조회 실패/오래된 자료/부족한 주간 표본을 구별한다. 샘플 수치로 채우지 않는다.

## 관심 종목 v1
소규모 국내 대형·유동성 후보군(config/universe.json)을 대상으로 한다. 전체 시장 순위가 아니다.
업종은 수익률 강세가 아닌 뉴스·이벤트상 주목으로 명시한다.
기사 제목에 정확한 기업명이 있고 긍정 키워드가 있으며 가격·거래량이 확인된 후보만 선정한다.
거래대금은 가격×거래량의 근사값임을 명시한다. 목표주가·매수가·실적 추정은 만들지 않는다.
이전 데일리와 비교해 신규/유지/근거 변경 상태를 표시한다. 근거 부족 시 0개를 허용한다.
기업명 매칭은 그룹/자회사 오매칭을 줄이기 위해 명시적 후보 이름만 사용하며 사람의 정밀 리서치를 대체하지 않는다.

## 운영과 롤백
STORAGE_BACKEND가 supabase일 때만 새 경로 사용. 미설정 시 기존 JSON·Pages 유지.
DB 모드에서 DB 장애를 JSON으로 숨기지 않는다. 마지막 공개 보고서는 유지하며 실행은 실패 처리.
관측 데이터와 결과는 트랜잭션으로 공개한다. 이미지 실패 시 이미지 없는 보고서 발행 가능.
업로드 후 DB 실패 시 이번 업로드 객체 삭제를 시도한다. 실패하면 운영 로그로 남긴다.
다시 json으로 변경하면 기존 경로로 복귀하나 DB 기간의 JSON 이력은 없으므로 전일/주간 이력에 공백이 생긴다.
기존 Pages 파일은 삭제하지 않는다. 새로운 서비스 주소는 SITE_URL로 Discord에 적용한다.
old Pages 자체에서의 리다이렉트는 Vercel 도메인 확정 후 별도 반영한다.

## 설정
Vercel 프로젝트 생성 완료 (2026-09-18):
- 프로젝트: ai-market-research-desk / prj_1TRMEWckyCUrvV7eEFwFKsyhkQso
- 팀: gwanghoons-projects
- 주소: https://ai-market-research-desk.vercel.app
- 파일 직접 배포. GitHub 저장소 자동 배포 연결은 아직 미설정.
- Next.js / apps/web / npm ci / npm run build / Node 22.x
- 서울(icn1) 선호 설정은 코드에 포함했으나 연결 배포 도구로 생성된 함수의 실제 응답은 iad1로 확인됨. 플랫폼 지역 설정 조정은 미완료.
- Supabase 환경변수는 시황용 프로젝트 선정 후 등록. 현재 연결된 DB는 기존 로또 서비스 DB로 확인되어 변경하지 않음.

GitHub Repository Secrets:
- SUPABASE_URL: https://<project>.supabase.co
- SUPABASE_SERVICE_ROLE_KEY: 배치 전용 service_role JWT 또는 Supabase secret key. 브라우저/Vercel에 넣지 않는다.
- 기존 AI_API_KEY / DART_API_KEY / DISCORD_WEBHOOK_URL 유지
GitHub Repository Variables:
- STORAGE_BACKEND: 검증 후 supabase (기본 json)
- SITE_URL: Vercel 최종 서비스 주소. Supabase 전환 시 필수.
Vercel 환경변수(서버 전용, NEXT_PUBLIC 접두사 불필요):
- SUPABASE_URL
- SUPABASE_PUBLISHABLE_KEY: publishable key 또는 legacy anon key. service_role 사용 금지.
Vercel Root Directory: apps/web, Framework: Next.js, Install: npm ci, Build: npm run build.
GitHub Secrets는 Vercel로 자동 전달되지 않는다. Vercel에도 위 읽기용 두 값을 별도 설정해야 한다.

## 승인 후 적용 절차
1. Supabase SQL Editor에서 supabase/migrations/001_research_desk.sql 실행.
2. Supabase URL·배치 키 Secrets 등록. storage bucket research-covers는 SQL에 포함.
3. Vercel 연결 및 환경변수 등록, 배포. 빈 데이터 안내 확인.
4. SITE_URL 설정 후 STORAGE_BACKEND=supabase 전환. 기존 QStash URL·Body는 유지.
5. 라이브/데일리 수동 실행, DB 행·화면·알림 확인. 주간은 데이터가 쌓인 뒤 실행.
6. anonymous 쓰기·pipeline_runs 조회가 거부되는지 확인.
외부 시스템 설정·실제 유료 API 실행·Discord 테스트 전송은 별도 승인 없이 하지 않는다.

## 로컬 검증 결과
- Python 62개 테스트: 기존 렌더링/분석 회귀, DB 입출력 계약, 종목 선정 조건, 중복 실행 및 저장 후 알림 순서.
- Next.js 프로덕션 빌드와 TypeScript 검사.
- PGlite 임시 PostgreSQL: 마이그레이션, 익명 RLS, 배치 RPC 권한, 중복 claim, 원자적 공개, 미래 관측시각 거부.
- Playwright: 데스크톱 1440px/모바일 390px, 5개 화면, 검색·펼쳐보기·호환 리다이렉트, 오류/빈 데이터 상태.
- 테스트는 가짜 시장 데이터와 임시 DB를 사용. 실제 Supabase 연결·Vercel 배포·외부 API 생성은 아직 미검증.
- 최초 운영 승인 시 실제 Supabase Storage 권한과 PostgREST 공개 스키마 설정도 확인해야 한다.
