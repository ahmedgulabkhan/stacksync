# STAGE 1
FROM debian:12-slim AS nsjail-builder
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates git build-essential pkg-config \
        libprotobuf-dev protobuf-compiler \
        libnl-route-3-dev libcap-dev libseccomp-dev \
        flex bison \
    && update-ca-certificates && rm -rf /var/lib/apt/lists/*
WORKDIR /src
RUN git clone --depth 1 https://github.com/google/nsjail.git
WORKDIR /src/nsjail
RUN make -j"$(nproc)"

# STAGE 2
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    NSJAIL_BIN=/usr/bin/nsjail \
    PYTHON_BIN=/usr/local/bin/python3

# Install runtime shared libs that nsjail needs
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
        libprotobuf32 \
        libseccomp2 \
        libcap2 \
        libnl-route-3-200 \
    && update-ca-certificates && rm -rf /var/lib/apt/lists/*

COPY --from=nsjail-builder /src/nsjail/nsjail /usr/bin/nsjail

RUN useradd -m -u 10001 appuser
WORKDIR /stacksync
COPY --chown=appuser:appuser requirements.txt ./requirements.txt
RUN pip install --no-cache-dir --no-compile -r requirements.txt
COPY --chown=appuser:appuser app.py ./app.py
USER appuser
EXPOSE 8080
CMD ["gunicorn", "-w", "2", "-b", "0.0.0.0:8080", "app:app"]


##########################################
############## WITHOUT NSJAIL ##############
##########################################
# FROM python:3.11-slim

# ENV PYTHONDONTWRITEBYTECODE=1 \
#     PYTHONUNBUFFERED=1 \
#     PIP_NO_CACHE_DIR=1

# WORKDIR /stacksync

# COPY requirements.txt /stacksync/requirements.txt
# COPY app.py /stacksync/app.py

# RUN pip install -r requirements.txt
# RUN useradd -m -u 10001 appuser
# USER appuser

# EXPOSE 8080

# CMD ["gunicorn", "-w", "2", "-b", "0.0.0.0:8080", "app:app"]