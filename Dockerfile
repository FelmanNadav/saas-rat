FROM python:3.11-slim

WORKDIR /app

# Install dependencies first for better layer caching
COPY requirements.client.txt .
RUN pip install --no-cache-dir -r requirements.client.txt

# Copy only client-relevant source files
COPY client.py common.py ./
COPY channel/    channel/
COPY crypto/     crypto/
COPY fragmenter/ fragmenter/

# -u = unbuffered stdout/stderr so logs appear immediately in docker logs
CMD ["python", "-u", "client.py"]
