FROM python:3.12-slim

WORKDIR /app
COPY . .
RUN pip install --no-cache-dir ".[ui]" \
    && useradd --create-home --uid 1000 app

USER app
EXPOSE 8000

# Upload mode: drop files or a ZIP in the browser, get a sorted ZIP back.
# Settings come from DOC_SORTER_* environment variables, e.g. DOC_SORTER_BASE_URL and DOC_SORTER_MODEL.
CMD ["doc-sorter", "serve", "--upload", "--host", "0.0.0.0", "--port", "8000"]
