FROM python:3.12-slim
WORKDIR /app
COPY lab/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
COPY lab /app/lab
ENV PYTHONPATH=/app
ENV LAB_TOKEN=local-lab
EXPOSE 8000
CMD ["uvicorn", "lab.gateway.main:app", "--host", "0.0.0.0", "--port", "8000"]
