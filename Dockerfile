FROM python:3.11
RUN apt-get update && apt-get install -y ffmpeg curl && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN mkdir -p /downloads /data && chmod 777 /downloads /data
EXPOSE 8000
ENV BUILD_REF=20260709v2
CMD ["python", "main.py"]
