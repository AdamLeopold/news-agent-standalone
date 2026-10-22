"""Keep import-time application initialization away from the running dataset."""
import os
from pathlib import Path
from tempfile import TemporaryDirectory

_runtime = TemporaryDirectory(prefix="ttf-unit-runtime-")
os.environ["TTF_NEWS_DATA_DIR"] = _runtime.name
os.environ.setdefault("TIKTOKEN_CACHE_DIR", str(Path(__file__).resolve().parents[1]/"data/cache/tokenizer"))
