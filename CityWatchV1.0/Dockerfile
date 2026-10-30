# CivicSync v1.0 — one node, two doors
# Works on Hugging Face Spaces (port 7860 default), Render, Fly.io, Railway —
# and locally:  docker build -t civicsync . && docker run -p 8000:8000 civicsync
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=7860

WORKDIR /node

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# app code + web doors + trained AI artifacts + demo replay corpus
COPY server.py config.json ./
COPY ai/ ai/
COPY web/ web/
COPY demoset/ demoset/
COPY data/ data/

EXPOSE 7860
CMD ["python", "-X", "utf8", "server.py"]
