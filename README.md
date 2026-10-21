# AI Market Briefing Pipeline

운영 화면: https://ai-market-research-desk.vercel.app

## 운영 구조

- Python 수집·분석 → Supabase 원본 보관 → 공개 전용 published_briefings → Next.js / Vercel
- GitHub Actions 기본 STORAGE_BACKEND는 supabase입니다. json은 기존 Pages 호환 경로입니다.
- AI 이미지 생성·표시는 폐기했습니다. 과거 Storage 파일은 삭제하지 않습니다.
- AI_API_KEY는 텍스트 분석에만 사용합니다. 이미지 모델·이미지 활성화 설정은 필요 없습니다.

## 데일리 편집

- 아침 08:00 KST: 미국장 결과, 확인된 뉴스, 국내장 상방·하방 확인 조건.
- 저녁 18:00 KST: 국내장 결과, 동일 날짜 아침 관점과 실제 관측 비교, 미국장 확인 조건.
- 개별주 시간외·선물·공식 경제일정·컨센서스는 미수집입니다. 없는 값은 생성하지 않습니다.
- 뉴스는 최대 5건, 원문 확보/목록 요약/제목만 구분. 본문 확보는 독립 사실 검증과 다릅니다.
- 시장 온도는 보조 지표이며 상승 확률·수익률이 아닙니다.

## 외부 스케줄 (QStash, Asia/Seoul)

데일리: `0 8,18 * * MON-FRI`
POST `https://api.github.com/repos/kimgwanghoon/ai-market-briefing-pipeline/actions/workflows/main.yml/dispatches`
Body: `{"ref":"master"}`

장중 정각: `50 8-14 * * MON-FRI` → 09:00~15:00
Body: `{"ref":"master","inputs":{"align_to_target":"true","target_minute":"00"}}`

장중 마지막: `20 15 * * MON-FRI` → 15:30
Body: `{"ref":"master","inputs":{"align_to_target":"true","target_minute":"30"}}`

장중 URL: `https://api.github.com/repos/kimgwanghoon/ai-market-briefing-pipeline/actions/workflows/intraday.yml/dispatches`
Authorization은 `Bearer <PAT>`이며 토큰은 QStash 비밀 헤더에만 저장합니다.
주간은 토요일 09:00 KST GitHub cron을 유지합니다.

## 설정

GitHub Secrets: AI_API_KEY, DART_API_KEY, DISCORD_WEBHOOK_URL, SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY.
GitHub Variables: SITE_URL (기본 운영 URL), STORAGE_BACKEND (기본 supabase).
Vercel Root Directory: apps/web. 환경변수: SUPABASE_URL, SUPABASE_PUBLISHABLE_KEY.
브라우저·웹 조회에는 service-role 키를 사용하지 않습니다.

## 운영 감시와 복구

publication-health.yml은 QStash와 독립적으로 예약분 발행·Discord 상태를 점검합니다.
평일 데일리 08:45/18:45, 장중 매 정각분 :45, 15:30분은 16:15 KST에 확인합니다.
주간 보고서는 토요일 10:00 KST에 해당 주 발행을 확인합니다.
GitHub cron 자체 지연·중단 및 Discord 장애까지 보장하는 감시가 아니며, 거래소 휴일 캘린더는 아직 연결하지 않았습니다.
실패 알림은 Actions 실행 링크만 전달하며 비밀 값·API 오류 본문을 기록하지 않습니다.
미발행: QStash 전달 → Actions 로그 → pipeline_runs 상태 순서로 확인합니다.
DB GET은 일시 오류만 제한적으로 재시도합니다. 발행·알림 POST는 자동 재생성하지 않습니다.
동일 목표의 완료된 발행은 재실행해도 중복 생성하지 않습니다. 실패한 목표는 원인 해결 후 수동 재실행합니다.

## 공개 데이터 전환

1. briefing_public_projection 마이그레이션으로 화면 전용 테이블·동기화 트리거·기존 보고서 복사.
2. 프런트가 published_briefings를 조회하는 배포 확인.
3. restrict_briefing_sources 적용으로 원본 테이블의 anon/authenticated SELECT 회수.
원본 분석·과거 파일은 삭제하지 않습니다. 로또 테이블은 이 프로젝트 변경 대상이 아닙니다.

## 검증

### 데일리 뉴스 브리핑

- 국내 증권·국제경제 기사와 미국 기술주 RSS를 함께 수집합니다. 아침은 최근 24시간(월요일은 72시간), 저녁은 당일 국내장 이후 범위를 사용합니다.
- 아침은 미국 관련 최대 3건, 저녁은 최대 2건을 우선 배정합니다. 관련 기사가 있으면 기술주 이슈 2건을 먼저 선별하며 전체는 최대 5건입니다.
- `daily_session.issues`에 사건 요약·국내 영향·다음 확인·해석 무효화 조건·출처와 기사 시각을 보관합니다. 기존 공개 데이터 투영에 포함되므로 별도 테이블은 필요 없습니다.
- 기사에 등장한 주요 미국 종목의 최근 정규장 관측값을 최대 8종목 수집합니다. 기사 이후 반응이나 시간외 주가로 단정하지 않습니다.
- AI 이슈 요약은 원문 연속 인용과 숫자를 검증하고 1회 교정합니다. 실패한 이슈만 출처 요약으로 대체합니다. 시장 요약도 문장 단위로 검증·교정하며 전체를 폐기하지 않습니다.
- 과거 발행본은 변경하지 않습니다. 새로운 구성은 다음 발행 또는 수동 실행으로 생성됩니다.

```sh
python -m unittest discover -s tests -v
cd apps/web
npm ci
npm run build
npm run typecheck
npm run test:research
npm run test:db
npm run test:ui
```

DB 계약 테스트는 로컬 임시 PGlite를 사용하며 운영 DB에 테스트 데이터를 쓰지 않습니다.
화면은 데스크톱/모바일·과거 보고서·빈 데이터·조회 실패 상태를 검증합니다.
