FROM node:22-bookworm-slim AS frontend
WORKDIR /build
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.10-slim AS app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 USE_TF=0 USE_FLAX=0
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/ backend/
COPY scripts/ scripts/
COPY docs/delivery/2026-10-11/FSP_CAREER_GUIDE.pdf docs/delivery/2026-10-11/FSP_CAREER_GUIDE.docx docs/delivery/2026-10-11/FSP_CAREER_PRESENTATION.pdf docs/delivery/2026-10-11/FSP_CAREER_PRESENTATION.pptx docs/delivery/2026-10-11/
COPY data/model_lock.json data/knowledge_manifest.json data/generation_model_lock.json data/
COPY data/assessment_content_v2.json resources/assessment_content_v2.json
COPY data/knowledge_manifest.json resources/knowledge_manifest.json
COPY data/screening_core_operations.json resources/screening_core_operations.json
COPY --from=frontend /build/dist/ frontend/dist/
RUN mkdir -p data artifacts && useradd --uid 10001 --create-home appuser && chown -R appuser:appuser /app
USER appuser
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health')"
CMD ["python","-m","uvicorn","backend.main:app","--host","0.0.0.0","--port","8000"]
