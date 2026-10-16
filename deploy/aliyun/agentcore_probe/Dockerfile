FROM python:3.12-slim-bookworm
RUN test -x /bin/bash && test -x /bin/sh \
    && groupadd --gid 10001 probe \
    && useradd --uid 10001 --gid 10001 --no-create-home probe \
    && mkdir -p /mnt/agenticfs/probe
WORKDIR /app
COPY probe.py /app/probe.py
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
USER 10001:10001
EXPOSE 8080
CMD ["python", "/app/probe.py"]
