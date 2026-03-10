# AI Market Briefing Pipeline

한미 주요 지수 데이터를 자동으로 수집하고, AI로 시황 핵심 포인트를 생성하는 자동화 브리핑 파이프라인입니다.

## 기술 스택

- **Python 3.11** - 메인 언어
- **OpenAI API** - 시황 요약, 헤드라인, 커버 이미지 생성
- **yfinance** - 미국 ETF/지수 데이터 수집
- **requests** - 네이버 국장 데이터 수집
- **Jinja2** - HTML 템플릿 렌더링
- **GitHub Actions** - 스케줄 실행 및 Pages 배포

## 주요 파일

| 파일 | 설명 |
|------|------|
| `main.py` | 지수 수집 → AI 브리핑 생성 → HTML 렌더링 → Discord 알림 실행 |
| `intraday.py` | 장중(08:30~15:30) 뉴스/공시/시장신호 하이브리드 스냅샷 생성 |
| `cleanup_json.py` | 30일 초과 JSON 데이터 정리 스크립트 |
| `weekly_report.py` | 주간 리포트(JSON/HTML) 생성 및 알림 전송 |
| `template.html` | 브리핑 페이지 HTML 템플릿 |
| `template_live.html` | 장중 라이브 페이지 HTML 템플릿 (`public/live.html`) |
| `template_weekly.html` | 주간 리포트 HTML 템플릿 (`public/weekly.html`) |
| `.github/workflows/main.yml` | 평일 오전/오후 자동 실행 및 GitHub Pages 배포 |
| `.github/workflows/intraday.yml` | 장중 매시 실행 및 intraday 데이터 배포 |
| `.github/workflows/cleanup-data.yml` | 평일 00:00(KST) JSON 정리 실행 |
| `.github/workflows/weekly-report.yml` | 토요일 09:00(KST) 주간 리포트 생성/배포 |
| `requirements.txt` | Python 의존성 |
| `public/data/*.json` | 실행 스냅샷 누적 데이터 (히스토리/대시보드용) |

## 환경 변수

로컬에서는 `.env` 파일을 사용합니다.

```env
AI_API_KEY=your_openai_api_key
DISCORD_WEBHOOK_URL=your_discord_webhook_url
GITHUB_PAGES_URL=https://<username>.github.io/<repository>/
GENERATE_AI_IMAGE=true
DART_API_KEY=your_opendart_api_key
```

- `AI_API_KEY`: OpenAI 요약/헤드라인/이미지 생성용 (없으면 fallback 모드)
- `DISCORD_WEBHOOK_URL`: 선택값, 설정 시 Discord 웹훅 전송
- `GITHUB_PAGES_URL`: 선택값, Discord 메시지 URL (미설정 시 GitHub Actions 환경 변수로 자동 추론)
- `GENERATE_AI_IMAGE`: `false`면 이미지 API 호출 없이 기존 커버 유지
- `DART_API_KEY`: 선택값, 장중 전자공시(OpenDART) 이벤트 분석용

## 스케줄 정책

- `main.yml`: 평일 08:00 / 18:00(KST) 데일리 브리핑
- `intraday.yml`: 평일 08:30~15:30(KST) 매시 장중 하이브리드 스냅샷
- `cleanup-data.yml`: 평일 00:00(KST) 30일 초과 JSON 자동 정리
- `weekly-report.yml`: 토요일 09:00(KST) 주간 시장 리포트 생성/발송

## 장중 점수 모델

- `intraday.py`는 `시장/뉴스/공시/섹터` 4개 컴포넌트를 결합해 raw 점수(-100~100)를 만들고, 이를 표시 점수(0~100)로 변환합니다.
- 최근 intraday 히스토리를 기반으로 컴포넌트 분포를 robust 정규화(중앙값/MAD)하고, 그리드 서치로 가중치를 보정합니다.
- 산출 결과에는 `model_version`, `weights`, `normalized_components`, `calibration_metric`, `calibration_samples`를 함께 기록합니다.
