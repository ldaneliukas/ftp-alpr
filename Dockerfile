FROM python:3.11-slim

# Install system dependencies for OpenVINO and image processing
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 \
    libglib2.0-0 \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy requirements first for better layer caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY alpr_ftp.py .

# Create upload directory
RUN mkdir -p /ftp/uploads

# Environment variables with defaults
ENV FTP_USER=camera \
    FTP_PASS=camera123 \
    FTP_PORT=21 \
    PASV_MIN=21000 \
    PASV_MAX=21010 \
    FTP_DIR=/ftp/uploads

# Expose FTP ports
EXPOSE 21 21000-21010

# Run the application
CMD ["python", "alpr_ftp.py"]

