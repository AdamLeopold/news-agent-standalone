from pathlib import Path
import os

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.environ.get("TTF_NEWS_DATA_DIR", str(ROOT / "data")))
DB_PATH = DATA_DIR / "ttf_news.sqlite3"
MODEL = os.environ.get("TTF_NEWS_MODEL", "gpt-oss:20b")
OLLAMA_URL = os.environ.get("TTF_NEWS_OLLAMA_URL", "http://127.0.0.1:11434")
PORT = int(os.environ.get("TTF_NEWS_PORT", "8765"))
POLL_SECONDS = int(os.environ.get("TTF_NEWS_POLL_SECONDS", "60"))
BACKFILL_START = "2024-05-15"
os.environ.setdefault("TIKTOKEN_CACHE_DIR", str(DATA_DIR / "cache" / "tokenizer"))

