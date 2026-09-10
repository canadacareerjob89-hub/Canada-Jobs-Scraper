FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends     build-essential     ca-certificates     && rm -rf /var/lib/apt/lists/*

WORKDIR /usr/src/app

# Install Python requirements
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY . ./

# Run the Apify entrypoint
CMD ["python", "-u", "apify_main.py"]
