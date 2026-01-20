#!/bin/bash
# Setup script for Revenue Copilot

set -e

echo "🚀 Setting up Revenue Copilot..."

# Backend setup
echo ""
echo "📦 Setting up backend..."
cd backend

# Create virtual environment
if [ ! -d "venv" ]; then
    python3 -m venv venv
fi

# Activate and install dependencies
source venv/bin/activate
pip install -r requirements.txt

# Run migrations
echo ""
echo "🗄️  Running database migrations..."
alembic upgrade head

echo ""
echo "✅ Backend setup complete!"

# Frontend setup
echo ""
echo "📦 Setting up frontend..."
cd ../frontend
npm install

echo ""
echo "✅ Frontend setup complete!"

echo ""
echo "=========================================="
echo "Setup complete! To start the app:"
echo ""
echo "Terminal 1 (Backend):"
echo "  cd backend && source venv/bin/activate"
echo "  uvicorn api.main:app --reload"
echo ""
echo "Terminal 2 (Frontend):"
echo "  cd frontend && npm run dev"
echo ""
echo "Then open http://localhost:5173"
echo "=========================================="
