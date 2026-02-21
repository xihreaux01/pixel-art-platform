FROM python:3.12-slim

RUN groupadd -r appuser && useradd -r -g appuser -d /opt/pixelart appuser

WORKDIR /opt/pixelart

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ src/
COPY alembic.ini .
COPY static/ static/

ENV PYTHONPATH=/opt/pixelart/src

RUN chown -R appuser:appuser /opt/pixelart
USER appuser

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
