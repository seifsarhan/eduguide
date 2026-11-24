import os
import secrets
from dotenv import load_dotenv
from groq import Groq
from google import genai

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV_PATH = os.path.join(BASE_DIR, ".env")
load_dotenv(ENV_PATH)


def _get_or_create_secret_key() -> str:
    key = os.getenv("SECRET_KEY", "").strip()
    if key and len(key) >= 24 and key not in ("your-secret-key-123", "edyguide-premium-secret"):
        return key
    generated = secrets.token_hex(32)
    try:
        lines = []
        if os.path.exists(ENV_PATH):
            with open(ENV_PATH, "r", encoding="utf-8") as f:
                lines = f.readlines()
        filtered = [ln for ln in lines if not ln.strip().startswith("SECRET_KEY=")]
        if filtered and not filtered[-1].endswith("\n"):
            filtered[-1] += "\n"
        filtered.append(f"SECRET_KEY={generated}\n")
        with open(ENV_PATH, "w", encoding="utf-8") as f:
            f.writelines(filtered)
        os.environ["SECRET_KEY"] = generated
    except Exception as e:
        print(f"WARN: Could not persist generated SECRET_KEY to .env: {e}")
    return generated


SECRET_KEY = _get_or_create_secret_key()
DEBUG_MODE = os.getenv("DEBUG", "False").strip().lower() in ("1", "true", "yes")
PORT = int(os.environ.get("PORT", 5001))

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GROQ_MODEL_NAME = os.getenv("GROQ_MODEL", "openai/gpt-oss-20b")
GROQ_FALLBACK_MODEL = "openai/gpt-oss-120b"
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL_NAME = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
GEMINI_FALLBACK_MODELS = [
    GEMINI_MODEL_NAME,
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-2.0-flash-lite",
]

groq_client = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None
gemini_client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None


def reload_ai_clients():
    """Reload API keys from .env after integration updates."""
    global GROQ_API_KEY, GEMINI_API_KEY, groq_client, gemini_client
    load_dotenv(ENV_PATH, override=True)
    GROQ_API_KEY = os.getenv("GROQ_API_KEY")
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
    groq_client = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None
    gemini_client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None
