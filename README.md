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
| `main.py` | 지수 수집 → AI 브리핑 생성 → HTML 렌더링 → Teams 알림 실행 |
| `template.html` | 브리핑 페이지 HTML 템플릿 |
| `.github/workflows/main.yml` | 평일 오전/오후 자동 실행 및 GitHub Pages 배포 |
| `requirements.txt` | Python 의존성 |
