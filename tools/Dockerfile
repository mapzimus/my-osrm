# my-osrm-tools: osmium CLI + pyosmium + shapely for PBF surgery.
#   docker build -t my-osrm-tools tools/
# Pinned by digest (python:3.12-slim multi-arch index, 2026-10-11); bump deliberately.
FROM python:3.12-slim@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1
RUN apt-get update \
    && apt-get install -y --no-install-recommends osmium-tool \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir osmium==4.0.2 shapely==2.0.6 pyshp==2.3.1
WORKDIR /work
