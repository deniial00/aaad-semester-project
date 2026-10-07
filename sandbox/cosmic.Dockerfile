FROM python:3.11-slim
COPY cosmic-requirements.txt /tmp/req.txt
RUN pip install --no-cache-dir -r /tmp/req.txt
WORKDIR /work
