import os
import time
from pathlib import Path

path=Path(os.getenv("DATABASE_PATH","data/bot.sqlite3")).with_suffix(".heartbeat")
try:healthy=time.time()-float(path.read_text())<180
except (OSError,ValueError):healthy=False
raise SystemExit(0 if healthy else 1)
