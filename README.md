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
| `template.html` | 브리핑 페이지 HTML 템플릿 |
| `.github/workflows/main.yml` | 평일 오전/오후 자동 실행 및 GitHub Pages 배포 |
| `requirements.txt` | Python 의존성 |
| `public/data/*.json` | 실행 스냅샷 누적 데이터 (히스토리/대시보드용) |

## 환경 변수

로컬에서는 `.env` 파일을 사용합니다.

```env
AI_API_KEY=your_openai_api_key
DISCORD_WEBHOOK_URL=your_discord_webhook_url
GITHUB_PAGES_URL=https://<username>.github.io/<repository>/
GENERATE_AI_IMAGE=true
```

- `AI_API_KEY`: OpenAI 요약/헤드라인/이미지 생성용 (없으면 fallback 모드)
- `DISCORD_WEBHOOK_URL`: 선택값, 설정 시 Discord 웹훅 전송
- `GITHUB_PAGES_URL`: 선택값, Discord 메시지 URL (미설정 시 GitHub Actions 환경 변수로 자동 추론)
- `GENERATE_AI_IMAGE`: `false`면 이미지 API 호출 없이 기존 커버 유지
