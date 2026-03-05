# market-briefing-hub

한미 주요 지수 데이터를 모아 AI 브리핑을 생성하고, `public/index.html` 정적 페이지를 만드는 프로젝트입니다.

## 로컬 실행

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python main.py
```

Windows PowerShell:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python main.py
```

생성 결과:
- `public/index.html`
- `public/cover.png` 또는 `public/cover.svg`

## 환경 변수

로컬에서는 `.env` 파일을 사용합니다.

```env
AI_API_KEY=your_openai_api_key
TEAMS_WEBHOOK_URL=optional_teams_webhook
GITHUB_PAGES_URL=https://<username>.github.io/<repository>/
```

- `AI_API_KEY`: OpenAI 요약/헤드라인/이미지 생성용 (없으면 fallback 모드)
- `TEAMS_WEBHOOK_URL`: 선택값, 설정 시 Teams 카드 전송
- `GITHUB_PAGES_URL`: 선택값, Teams 카드 버튼 URL

## GitHub Actions / Pages

1. 저장소 `Settings > Pages`에서 Source를 `GitHub Actions`로 설정
2. 저장소 Secrets에 필요 값 등록
   - `AI_API_KEY` (권장)
   - `TEAMS_WEBHOOK_URL` (선택)
   - `GITHUB_PAGES_URL` (선택)
3. `.github/workflows/main.yml` 수동 실행 또는 스케줄 실행
