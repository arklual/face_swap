#!/bin/bash
set -e

echo "=== Face Transfer Backend Quick Start ==="
echo ""

if [ ! -f "backend/.env" ]; then
    echo "⚠️  .env file not found. Copying from example..."
    cp backend/env.example backend/.env
    echo "✅ Created backend/.env"
    echo "⚠️  Please edit backend/.env with your S3 credentials before continuing!"
    echo ""
    read -p "Press Enter to continue after editing .env file..."
fi

if ! command -v docker &> /dev/null; then
    echo "❌ Docker is not installed. Please install Docker first."
    exit 1
fi

if ! command -v docker-compose &> /dev/null; then
    echo "❌ Docker Compose is not installed. Please install Docker Compose first."
    exit 1
fi

echo "✅ Docker and Docker Compose found"

if command -v nvidia-smi &> /dev/null; then
    echo "✅ NVIDIA GPU detected:"
    nvidia-smi --query-gpu=name,memory.total --format=csv,noheader
else
    echo "⚠️  NVIDIA GPU not detected or nvidia-smi not available"
    echo "   The application will still run but inference will be slow on CPU"
fi

echo ""
echo "Starting services..."
echo ""

docker-compose pull db redis
docker-compose build

docker-compose up -d

echo ""
echo "Waiting for services to be ready..."
sleep 10

echo ""
echo "Checking API health..."
if curl -s http://localhost:8000/health | grep -q "ok"; then
    echo "✅ API is healthy!"
else
    echo "⚠️  API health check failed. Check logs with: docker-compose logs"
fi

echo ""
echo "=== Quick Start Complete ==="
echo ""
echo "📊 View logs: docker-compose logs -f"
echo "🔍 Check status: docker-compose ps"
echo "🌐 API documentation: http://localhost:8000/docs"
echo "💻 API endpoint: http://localhost:8000"
echo ""
echo "To stop: docker-compose down"
echo ""