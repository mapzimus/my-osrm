# my-osrm-tools: osmium CLI + pyosmium + shapely for PBF surgery.
#   docker build -t my-osrm-tools tools/
FROM python:3.12-slim
RUN apt-get update \
    && apt-get install -y --no-install-recommends osmium-tool \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir osmium==4.0.2 shapely==2.0.6
WORKDIR /work
