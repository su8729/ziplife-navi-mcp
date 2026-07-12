# 집생활 내비 (ZipLife Navi) MCP 서버 — PlayMCP in KC 배포용
FROM python:3.12-slim

# 로그가 버퍼링 없이 바로바로 찍히게 (컨테이너 로그 확인용)
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# 의존성 먼저 설치 (코드보다 먼저 복사해야 캐시가 잘 재사용됨)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 서버 코드 복사
COPY server.py .

# FastMCP 기본 포트. server.py에서 PORT 환경변수로 덮어쓸 수 있게 되어 있음.
EXPOSE 8000

# API 키(YOUTHCENTER_API_KEY, PUBLIC_DATA_API_KEY)는 여기서 절대 설정하지 않는다.
# 이미지에 키를 박아넣으면 GitHub에 그대로 노출되므로,
# PlayMCP 등록 화면(또는 상세정보 > 환경변수 설정)에서 별도로 주입해야 한다.

CMD ["python", "server.py"]
