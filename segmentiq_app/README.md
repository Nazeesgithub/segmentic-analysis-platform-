# SegmentIQ Platform App

SegmentIQ is an AI-powered customer intelligence platform built on your trained ML artifacts.

## Features

- K-Means customer segmentation (4 business segments)
- Random Forest segment prediction API
- PCA-powered dashboard scatter visualization
- Correlation heatmap and feature-importance charts
- NOVA AI assistant (rule-based, optional Groq enhancement)
- Admin upload + retrain workflow

## Project Structure

- app.py: Flask backend and API routes
- templates/index.html: Frontend pages (Home, Dashboard, Predictor, AI Assistant, Admin)
- static/styles.css: UI theme and layout
- static/app.js: Frontend logic and chart rendering

## Run

1. Open a terminal in the workspace root.
2. Activate virtual environment.
3. Run:

```powershell
python segmentiq_app/app.py
```

4. Open:

http://localhost:8000

## Optional: Groq API for NOVA

Set environment variable before running the app:

```powershell
$env:GROQ_API_KEY="your_groq_api_key"
python segmentiq_app/app.py
```

If no key is set, NOVA automatically uses the built-in rule-based business assistant.

For local development, you can also create a `.env` file in the workspace root with:

```env
GROQ_API_KEY=your_groq_api_key
```

## API Endpoints

- GET /api/health
- GET /api/features
- POST /api/predict
- GET /api/dashboard
- GET /api/algorithm-fit
- POST /api/nova-chat
- POST /api/admin/upload
- POST /api/admin/retrain

## Deployment

### Why Netlify deployment fails here

This project is a stateful Flask backend (server process, SQLite file, ML artifacts and retraining flow).
Netlify is mainly for static sites and serverless functions, so full Flask apps like this often fail or behave incorrectly there.

### Recommended: Deploy Flask on Render

This repository is now prepared for Render with:

- `Procfile`
- `render.yaml`
- `gunicorn` in dependencies

Steps:

1. Push this project to GitHub.
2. Open Render and create a **New Web Service** from your repo.
3. Render auto-detects `render.yaml`.
4. Set environment variables:
   - `GROQ_API_KEY` (optional, for NOVA AI)
   - `FLASK_SECRET_KEY` (already auto-generated from `render.yaml`)
5. Deploy.

The service will run with:

```bash
gunicorn --chdir segmentiq_app app:app
```

### If you must use Netlify

Use Netlify only for a static frontend and host the Flask backend on Render/Railway/Heroku, then call that backend URL from your frontend.
