# NewsBot v4 — تصویر اجرای production
FROM python:3.12-slim

WORKDIR /app

# وابستگی‌ها اول (لایه‌ی کش‌شونده)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# کد
COPY run.py ./
COPY newsbot/ ./newsbot/

ENV PYTHONUNBUFFERED=1 \
    DB_FILE=/app/data/newsbot.db

# کاربر غیرroot + پوشه‌ی داده‌ی ماندگار
RUN useradd -m -u 10001 newsbot \
    && mkdir -p /app/data \
    && chown -R newsbot:newsbot /app
USER newsbot
VOLUME ["/app/data"]

# خودآزمایی هنگام ساخت تصویر (بدون توکن واقعی فقط syntax/import بررسی می‌شود)
RUN python -m py_compile run.py newsbot/*.py

CMD ["python", "run.py"]
