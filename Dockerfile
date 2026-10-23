FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HOST=0.0.0.0 PORT=8000 DATA_DIR=/app/data
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt && useradd --create-home --uid 10001 brsy
COPY brsy ./brsy
COPY web ./web
COPY deploy/docker-entrypoint.py /usr/local/bin/brsy-entrypoint.py
RUN mkdir /app/data && chown brsy:brsy /app/data
EXPOSE 8000
ENTRYPOINT ["python", "/usr/local/bin/brsy-entrypoint.py"]
CMD ["python", "-m", "brsy"]
