# EduGuide — Smart University Platform

Multi-university student platform with an AI academic advisor, QR attendance, GPA tools and Egyptian payments.

![EduGuide — Smart University Platform screenshot](docs/screenshot.webp)

## Features

- Student, instructor, university-admin and system-admin portals
- AI advisor chat, CV optimizer and mock interviews (Groq + Gemini)
- QR lecture check-in with automatic absence alerts
- GPA calculator, study planner, quizzes and QR-verified transcripts
- Paymob card and Fawry payments with receipts
- Per-university branding, CSRF protection, security headers and audit logs
- Arabic / English interface

## Tech stack

Python · Flask · MySQL / SQLite · Groq & Gemini AI · Paymob

## Run it locally

```bash
pip install -r requirements.txt
cp .env.example .env      # add GROQ_API_KEY, GEMINI_API_KEY, Paymob keys
python app.py             # http://localhost:5001
```

Deployment notes for Render and Docker are in [DEPLOYMENT.md](DEPLOYMENT.md).

---

Built by [Seif Sarhan](https://seifsarhan.github.io) · [GitHub](https://github.com/seifsarhan)
