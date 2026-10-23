FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HOST=0.0.0.0
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt && useradd --create-home --uid 10001 brsy
COPY brsy ./brsy
COPY web ./web
RUN mkdir /app/data && chown brsy:brsy /app/data
USER brsy
EXPOSE 8000
CMD ["python", "-m", "brsy"]
