# AI Market Briefing Pipeline

한미 주요 지수 데이터를 자동으로 수집하고, AI로 시황 핵심 포인트를 생성하는 자동화 브리핑 파이프라인입니다.

## 기술 스택

- **Python 3.11** - 메인 언어
- **OpenAI API** - 시황 요약, 헤드라인, 커버 이미지 생성
- **yfinance** - 미국 ETF/지수 데이터 수집
- **requests** - 네이버 국장 데이터 수집
- **Jinja2** - HTML 템플릿 렌더링
- **GitHub Actions** - 스케줄 실행 및 Pages 배포

브리핑 화면은 한국·미국·리스크 지표 8종, 시장 온도차를 반영한 헤드라인, AI 편집 커버, 그리고 주요 뉴스의 영향 범위와 `왜 중요한가` 설명을 함께 제공합니다. 뉴스 선택 결과는 원문 제목과 URL을 그대로 유지하며 후보 기사에 포함된 정보 범위 안에서만 편집 설명을 생성합니다.

## 주요 파일

| 파일 | 설명 |
|------|------|
| `main.py` | 지수 수집 → AI 브리핑 생성 → HTML 렌더링 → Discord 알림 실행 |
| `intraday.py` | 장중(08:30~15:30) 뉴스/공시/시장신호 하이브리드 스냅샷 생성 |
| `cleanup_json.py` | 30일 초과 JSON 데이터 정리 스크립트 |
| `weekly_report.py` | 일별 센티먼트·주간 지수 변화·이벤트·다음 주 조건부 전망을 집계해 JSON/HTML 및 알림 생성 |
| `template.html` | 브리핑 페이지 HTML 템플릿 |
| `template_live.html` | 장중 라이브 페이지 HTML 템플릿 (`public/live.html`) |
| `template_weekly.html` | 주간 리포트 HTML 템플릿 (`public/weekly.html`) |
| `.github/workflows/main.yml` | 평일 오전/오후 자동 실행 및 GitHub Pages 배포 |
| `.github/workflows/intraday.yml` | 장중 매시 실행 및 intraday 데이터 배포 |
| `.github/workflows/cleanup-data.yml` | 평일 00:00(KST) JSON 정리 실행 |
| `.github/workflows/weekly-report.yml` | 토요일 09:00(KST) 주간 리포트 생성/배포 |
| `requirements.txt` | Python 의존성 |
| `ai_generation.py` | JSON Schema 기반 AI 출력 계약과 모델 설정 |
| `tests/` | 점수 스키마, HTML 안전성, 구조화 출력 회귀 테스트 |
| `public/data/*.json` | 실행 스냅샷 누적 데이터 (히스토리/대시보드용) |

## 환경 변수

로컬에서는 `.env` 파일을 사용합니다.

```env
AI_API_KEY=your_openai_api_key
DISCORD_WEBHOOK_URL=your_discord_webhook_url
GITHUB_PAGES_URL=https://<username>.github.io/<repository>/
GENERATE_AI_IMAGE=true
DART_API_KEY=your_opendart_api_key
OPENAI_TEXT_MODEL=gpt-4o-mini-2024-07-18
OPENAI_IMAGE_MODEL=gpt-image-1-mini
MIN_MARKET_COVERAGE=6
MIN_WEEKLY_SAMPLES=6
```

- `AI_API_KEY`: OpenAI 요약/헤드라인/이미지 생성용 (없으면 fallback 모드)
- `DISCORD_WEBHOOK_URL`: 선택값, 설정 시 Discord 웹훅 전송
- `GITHUB_PAGES_URL`: 선택값, Discord 메시지 URL (미설정 시 GitHub Actions 환경 변수로 자동 추론)
- `GENERATE_AI_IMAGE`: `false`면 이미지 API 호출 없이 기존 커버 유지
- `DART_API_KEY`: 선택값, 장중 전자공시(OpenDART) 이벤트 분석용
- `OPENAI_TEXT_MODEL`: 구조화 출력을 지원하는 텍스트 모델 (기본값은 재현 가능한 스냅샷 고정)
- `OPENAI_IMAGE_MODEL`: 커버 이미지 모델 (기본값 `gpt-image-1-mini`)
- `MIN_MARKET_COVERAGE`: 데일리 배포에 필요한 최소 유효 지표 수. 미달 시 기존 정상 페이지를 덮어쓰지 않고 작업을 실패시킵니다.
- `MIN_WEEKLY_SAMPLES`: 주간 리포트 배포에 필요한 최소 장중 스냅샷 수 (기본값 6)

## 로컬 검증

```bash
python -m compileall -q .
python -m unittest discover -s tests -v
```

배포 워크플로는 `gh-pages`의 기존 `data/`를 복원한 뒤 생성 작업을 실행합니다. 모든 Pages 쓰기 작업은 공통 concurrency group으로 직렬화되어 히스토리 유실과 동시 push 충돌을 줄입니다.

## 스케줄 정책

- `main.yml`: 평일 08:00 / 18:00(KST) 데일리 브리핑
- `intraday.yml`: 평일 08:10~15:10(KST)에 준비를 시작하고 08:30~15:30 정각에 장중 스냅샷 생성을 시도합니다. GitHub 스케줄러가 늦게 시작되면 대기 없이 실제 시작 시각에 생성합니다.
- `cleanup-data.yml`: 평일 00:00(KST) 30일 초과 JSON 자동 정리
- `weekly-report.yml`: 토요일 09:00(KST) 주간 시장 리포트 생성/발송

## 장중 점수 모델

- `intraday.py`는 `시장/뉴스/공시/섹터` 4개 컴포넌트를 결합해 raw 점수(-100~100)를 만들고, 이를 표시 점수(0~100)로 변환합니다.
- 장중 히트맵과 타임라인은 예약 슬롯으로 보정하지 않고, 워크플로가 실제 실행해 저장한 KST 시각의 스냅샷만 표시합니다.
- 예약 실행 스냅샷에는 목표 시각과 실제 생성 지연 시간을 `execution` 메타데이터로 기록합니다.
- 최근 intraday 히스토리를 기반으로 컴포넌트 분포를 robust 정규화(중앙값/MAD)하고, 그리드 서치로 가중치를 보정합니다.
- 산출 결과에는 `model_version`, `weights`, `normalized_components`, `calibration_metric`, `calibration_samples`를 함께 기록합니다.
