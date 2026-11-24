# EDUGUIDE University Platform — Deployment Guide

## Option 1: Render.com (Fastest — Free HTTPS Domain in 3 Minutes)
1. Push this project to a private GitHub repository (`.env` is already gitignored!).
2. Go to https://render.com -> **New** -> **Blueprint** and connect the repository (`render.yaml` is already configured).
3. In Render Dashboard -> **Environment**, add:
   - `GROQ_API_KEY`
   - `GEMINI_API_KEY`
   - `PAYMOB_API_KEY`, `PAYMOB_INTEGRATION_ID_CARD`, `PAYMOB_INTEGRATION_ID_FAWRY`
   - Optional managed MySQL (`DB_HOST`, `DB_USER`, `DB_PASS`, `DB_NAME`) — or leave `DB_HOST` empty to use the bundled SQLite database immediately for demos!
4. Deploy! You get an automatic `https://eduguide-university-platform.onrender.com` URL.

## Option 2: Docker (University On-Premise / VPS)
```bash
docker build -t eduguide-platform .
docker run -d -p 5001:5001 --env-file .env --name eduguide eduguide-platform
```

## First-Time Database Hardening / Migration
Run once against a fresh database:
```bash
python migrations/upgrade_db.py
python migrations/seed_enterprise.py
```
