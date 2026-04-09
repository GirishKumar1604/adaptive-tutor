FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DEFAULT_TIMEOUT=180

# System deps for Manim CE + LaTeX + rendering
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    pkg-config \
    build-essential \
    ghostscript \
    libcairo2 \
    libcairo2-dev \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    libpango1.0-dev \
    libgirepository1.0-dev \
    gir1.2-pango-1.0 \
    # LaTeX stack
    texlive-latex-base \
    texlive-latex-extra \
    texlive-fonts-recommended \
    texlive-fonts-extra \
    texlive-science \
    dvisvgm \
    && rm -rf /var/lib/apt/lists/*


WORKDIR /app
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
RUN python -c "import groq; print('groq OK')"


COPY app /app/app
WORKDIR /app/app

# Make sure output dir exists
RUN mkdir -p /app/app/storage/videos
