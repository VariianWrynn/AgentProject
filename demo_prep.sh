#!/bin/bash
set -e

echo "=== AgentProject Demo Prep ==="

# Step 1: Start Docker services
echo "[1/4] Starting Docker services..."
docker compose up -d
echo "Waiting 45s for Milvus to initialize..."
sleep 45

# Step 2: Health check
echo "[2/4] Checking service health..."
curl -s http://localhost:8003/health | python -m json.tool

# Step 3: Warmup demo question (~209s for first run, instant if cached)
echo "[3/4] Warming up demo question (~209s on first run)..."
echo "Question: 分析中国储能行业2024年的竞争格局和技术趋势"
WARMUP=$(curl -s -X POST "http://localhost:8003/demo/warmup")
echo "Warmup result: $WARMUP"

# Step 4: Start frontend dev server
echo "[4/4] Starting frontend dev server..."
cd "$(dirname "$0")/frontend"
npm run dev &

echo ""
echo "Demo ready at http://localhost:5173"
echo "Demo question pre-cached. First response will be ~20s replay."
echo "For ad-hoc questions, toggle '快速模式' for ~40s response."
