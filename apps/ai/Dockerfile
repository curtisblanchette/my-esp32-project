FROM python:3.12-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY config/ config/
COPY src/ src/

# Set Python path
ENV PYTHONPATH=/app

# Run the orchestrator
CMD ["python", "-m", "src.main"]
