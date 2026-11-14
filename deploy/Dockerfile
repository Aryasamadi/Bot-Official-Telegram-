# NewsBot v4 — تصویر اجرای production
# نسخهٔ پایتون با CI و VPS هم‌تراز است (۳.۱۳)؛ digest پین شده تا rebuild تغییر ناگهانی ندهد.
#
# DEVOPS-03: این ایمیج *immutable* است — کد و وابستگی‌ها داخلش پخته می‌شوند و
# هیچ bind-mountِ کدی (مثل /opt/Bot-Official-Telegram:/app) وجود ندارد.
# استقرار = build با تگِ SHA + اجرای کانتینر از همان تگ (docker-compose.yml).
# 2026-11-06: bumped to a newer 3.13-slim digest (Debian 13.7, apt 3.0.4 => CVE-2026-109518 fixed;
# pip 26.2.2 with vendored certifi 2026.10.19 => GHSA-6q48-6c7w-9gw2 fixed).
FROM python:3.15-slim@sha256:65a65fc7b30b06cb6df4930fd4650697cb23f4c2698ab81fc87e4b031bc36928

WORKDIR /app

# امنیت: CVE-2026-76844 / CVE-2026-85091 (zlib1g) در digest پین‌شدهٔ بالا هنوز رفع نشده؛
# نسخهٔ اصلاح‌شده (1.3.1-1+deb13u1) در Debian 13 موجود است. فقط همین بسته ارتقا می‌یابد
# (نه upgrade کامل) تا ایمیج قابل‌پیش‌بینی بماند. با bump بعدیِ digest می‌توان این بلوک را حذف کرد.
# عمداً بدون پین: هدف دریافت جدیدترین build امنیتیِ zlib1g است و پینِ دقیق با آپدیت بعدی Debian build را می‌شکند.
# hadolint ignore=DL3008
RUN apt-get update \
    && apt-get install -y --no-install-recommends --only-upgrade zlib1g \
    && rm -rf /var/lib/apt/lists/*

# نسخهٔ کد (SHA) هنگامِ build تزریق می‌شود تا پنلِ «نسخهٔ در حال اجرا» درست باشد.
ARG NEWSBOT_VERSION=unknown
ENV NEWSBOT_VERSION=${NEWSBOT_VERSION}

# وابستگی‌ها اول (لایه‌ی کش‌شونده) — فقط نسخه‌های قفل‌شدهٔ production
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# کد
COPY run.py ./
COPY newsbot/ ./newsbot/

ENV PYTHONUNBUFFERED=1 \
    DB_FILE=/app/data/newsbot.db \
    DRAIN_FLAG_FILE=/tmp/newsbot_draining \
    DRAINED_FLAG_FILE=/tmp/newsbot_drained

# کاربر غیرroot + پوشه‌ی داده‌ی ماندگار
RUN useradd -m -u 10001 newsbot \
    && mkdir -p /app/data \
    && chown -R newsbot:newsbot /app
USER newsbot
VOLUME ["/app/data"]

# خودآزمایی هنگام ساخت تصویر (بدون توکن واقعی فقط syntax/import بررسی می‌شود)
RUN python -m py_compile run.py newsbot/*.py

# healthcheck سبک: بسته import شود، پوشه‌ی داده نوشتنی باشد، دیتابیس باز شود.
# (همان دستوری که healthcheck در docker-compose.yml اجرا می‌کند)
HEALTHCHECK --interval=60s --timeout=10s --start-period=30s --retries=3 \
    CMD python -m newsbot.healthcheck

CMD ["python", "run.py"]
