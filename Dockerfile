FROM python:3.11-slim

# Install system dependencies
RUN apt-get update && apt-get install -y \
    tesseract-ocr \
    libtesseract-dev \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender-dev \
    ffmpeg \
    python3-opencv \
 && apt-get clean \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy requirements file and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy Google Vision credentials from local folder into the container
COPY google_vision_json/ ./google_vision_json/

# Set the environment variable so that the Google Vision API knows where to find the credentials.
ENV GOOGLE_APPLICATION_CREDENTIALS="/app/google_vision_json/eastern-store-455819-u7-6d76619e02a8.json"

# Copy your entire application code into the container
COPY . .

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
