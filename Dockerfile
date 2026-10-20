FROM python:3.12-slim
WORKDIR /progetto
RUN apt-get update && apt-get install -y --no-install-recommends openssl espeak-ng \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 8000
CMD ["python", "run.py", "--lan", "--no-browser"]
