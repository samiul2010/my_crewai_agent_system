# ── Base image ────────────────────────────────────────────────────────────────
FROM python:3.11-slim

# ── System dependencies ───────────────────────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    git \
    && rm -rf /var/lib/apt/lists/*

# ── Non-root user (required by Hugging Face Spaces) ──────────────────────────
RUN useradd -m -u 1000 user
ENV HOME=/home/user \
    PATH=/home/user/.local/bin:$PATH

# ── Install Playwright and Chromium ──────────────────────────────────────────
RUN pip install --no-cache-dir playwright>=1.40.0 && \
    playwright install chromium && \
    playwright install-deps chromium
# ── Working directory ─────────────────────────────────────────────────────────
WORKDIR $HOME/app

# ── Install Python dependencies BEFORE copying code (layer cache) ─────────────
COPY --chown=user requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt


# ── Copy application code ─────────────────────────────────────────────────────
COPY --chown=user . .

# ── Switch to non-root user ───────────────────────────────────────────────────
USER user

# ── Expose status web UI port (HF Spaces default) ─────────────────────────────
EXPOSE 7860

# ── Healthcheck ────────────────────────────────────────────────────────────────
HEALTHCHECK --interval=60s --timeout=10s --start-period=30s \
    CMD curl -f http://localhost:7860/health || exit 1

# ── Launch bot ────────────────────────────────────────────────────────────────
CMD ["python", "telegram_bot.py"]
