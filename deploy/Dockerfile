# NewsBot v4 — تصویر اجرای production
# نسخهٔ پایتون با CI و VPS هم‌تراز است (۳.۱۳)؛ digest پین شده تا rebuild تغییر ناگهانی ندهد.
FROM python:3.13-slim@sha256:70729b46c69b4f1e97c4822c1af3df53a1476cf5ddc6c087c0c10bc3a5678c2f

WORKDIR /app

# وابستگی‌ها اول (لایه‌ی کش‌شونده) — فقط نسخه‌های قفل‌شدهٔ production
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

# خودآزماییِ دوره‌ایِ پیکربندی/دیتابیس (env واقعیِ کانتینر را می‌خواند؛ پریود بلند تا هزینه کم)
HEALTHCHECK --interval=5m --timeout=60s --start-period=30s --retries=2 \
    CMD python run.py --check

CMD ["python", "run.py"]
