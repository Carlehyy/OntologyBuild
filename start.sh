#!/bin/bash
# Start script for Ontology-Graph-AI Framework
# Usage: ./start.sh

set -e

echo "🚀 Starting Ontology-Graph-AI Framework..."

# Check Python
echo "✓ Python: $(python3 --version)"

# Start Backend
echo ""
echo "📡 Starting Backend API (port 8000)..."
cd backend
python3 -c "import uvicorn; uvicorn.run('app.main:app', host='0.0.0.0', port=8000, log_level='info')" &
BACKEND_PID=$!
cd ..

echo "   Backend PID: $BACKEND_PID"

# Wait for backend
sleep 3

# Check if backend is running
if curl -s http://localhost:8000/ > /dev/null 2>&1; then
    echo "   ✓ Backend is running"
else
    echo "   ✗ Backend failed to start"
    kill $BACKEND_PID 2>/dev/null
    exit 1
fi

# Seed data
echo ""
echo "🌱 Seeding database..."
curl -s -X POST http://localhost:8000/api/v1/seed > /dev/null 2>&1 && echo "   ✓ Seed complete" || echo "   ⚠ Seed may have already been run"

# Start Frontend
echo ""
echo "🌐 Starting Frontend (port 5173)..."
npx serve dist -l 5173 &
FRONTEND_PID=$!

echo "   Frontend PID: $FRONTEND_PID"
echo ""
echo "========================================"
echo "✅ System started!"
echo ""
echo "   Frontend: http://localhost:5173"
echo "   Backend:  http://localhost:8000"
echo "   API Docs: http://localhost:8000/docs"
echo ""
echo "Press Ctrl+C to stop"
echo "========================================"

# Wait for interrupt
trap "kill $BACKEND_PID $FRONTEND_PID 2>/dev/null; exit 0" INT TERM
wait
