# Optional: AWS App Runner, ECS, or local `docker run -p 8000:8000`
FROM python:3.11-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 8000
# Render/Fly/etc. set PORT; default 8000 for local `docker run -p 8000:8000`
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}"]
