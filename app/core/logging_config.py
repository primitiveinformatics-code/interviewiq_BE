"""
Centralized logging configuration for InterviewIQ.
All logs are written to the logs/ directory with rotation.
"""

import logging
import os
from logging.handlers import RotatingFileHandler

LOG_DIR = os.environ.get("LOG_DIR") or os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "logs")
)
os.makedirs(LOG_DIR, exist_ok=True)

LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)-30s | %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

def get_logger(name: str) -> logging.Logger:
    """Return a logger that writes to both console and logs/interviewiq.log."""
    logger = logging.getLogger(name)

    if logger.handlers:
        return logger  # avoid duplicate handlers on reimport

    logger.setLevel(logging.DEBUG)

    # ── File handler (rotating, 5 MB, keep 5 backups) ─────────
    file_handler = RotatingFileHandler(
        os.path.join(LOG_DIR, "interviewiq.log"),
        maxBytes=5 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT))

    # ── Console handler (INFO and above) ──────────────────────
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT))

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

    return logger
