import logging
import re
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

# Patterns to mask sensitive data
MASK_PATTERns = [
    (re.compile(r"bot\d+:[A-Za-z0-9_-]+"), "bot***:SECRET_TOKEN"),
    (re.compile(r"(ndus=)([^;]+)", re.IGNORECASE), r"\1[MASKED]"),
    (re.compile(r"(token=)([a-zA-Z0-9_-]+)", re.IGNORECASE), r"\1[MASKED]"),
    (re.compile(r"(password=)([^\s&]+)", re.IGNORECASE), r"\1[MASKED]"),
    (re.compile(r"(pwd=)([^\s&]+)", re.IGNORECASE), r"\1[MASKED]"),
]


class SafeFormatter(logging.Formatter):
    """Custom formatter to redact sensitive secrets, bot tokens, and cookies."""

    def format(self, record: logging.LogRecord) -> str:
        original = super().format(record)
        masked = original
        for pattern, replacement in MASK_PATTERns:
            masked = pattern.sub(replacement, masked)
        return masked


def setup_logger(name: str = "terabox_bot", log_level: str = "INFO") -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    level = getattr(logging, log_level.upper(), logging.INFO)
    logger.setLevel(level)

    log_format = "%(asctime)s | %(levelname)-8s | %(name)s:%(funcName)s:%(lineno)d - %(message)s"
    formatter = SafeFormatter(log_format)

    # Console Handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(level)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # File Handler
    log_dir = Path("./logs")
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            log_dir / "bot.log",
            maxBytes=10 * 1024 * 1024,  # 10 MB
            backupCount=5,
            encoding="utf-8"
        )
        file_handler.setLevel(level)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    except Exception as e:
        console_handler.handle(
            logging.LogRecord("terabox_bot", logging.WARNING, __file__, 0, f"Could not init file log: {e}", (), None)
        )

    logger.propagate = False
    return logger


logger = setup_logger()
