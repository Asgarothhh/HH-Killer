import logging
import sys
from pathlib import Path


APP_LOGGER_NAME = "HH-Killer"


def setup_applevel_logger(logger_name=APP_LOGGER_NAME, file_name=None):
    logger = logging.getLogger(logger_name)
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    logger.handlers.clear()

    stream = sys.stdout
    try:
        stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    sh = logging.StreamHandler(stream)
    sh.setFormatter(formatter)
    logger.addHandler(sh)

    if file_name:
        log_path = Path(file_name)
        if log_path.parent and str(log_path.parent) not in ("", "."):
            log_path.parent.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(log_path, encoding="utf-8")
        fh.setFormatter(formatter)
        logger.addHandler(fh)

    return logger


def get_logger(module_name):
    return logging.getLogger(APP_LOGGER_NAME).getChild(module_name)