FROM python:3.12-slim
WORKDIR /app
COPY backend/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
COPY backend /app/backend
COPY replay /app/replay
ENV PYTHONPATH=/app/backend
ENV CHAOSMEDIC_MODE=live
ENV CHAOSMEDIC_DIAGNOSER=rules
ENV CHAOSMEDIC_DB=/data/chaosmedic.db
EXPOSE 8080
CMD ["uvicorn", "chaosmedic.main:app", "--host", "0.0.0.0", "--port", "8080"]
