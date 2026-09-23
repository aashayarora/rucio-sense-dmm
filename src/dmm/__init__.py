import os
import sys
import logging
from logging.handlers import RotatingFileHandler

# Keep the log on disk bounded and split into chunks so it stays greppable.
# Overridable via env without touching code.
_LOG_FILE = os.environ.get("DMM_LOG_FILE", "dmm.log")
_LOG_MAX_BYTES = int(os.environ.get("DMM_LOG_MAX_BYTES", 10 * 1024 * 1024))  # 10 MB per chunk
_LOG_BACKUP_COUNT = int(os.environ.get("DMM_LOG_BACKUP_COUNT", 10))          # keep 10 old chunks

logging.basicConfig(
    format="(%(threadName)s) [%(asctime)s] %(levelname)s: %(message)s",
    datefmt="%m-%d-%Y %H:%M:%S %p",
    level=logging.INFO,  # DMM() applies [dmm] log_level once the config is loaded
    handlers=[
        RotatingFileHandler(
            filename=_LOG_FILE,
            maxBytes=_LOG_MAX_BYTES,
            backupCount=_LOG_BACKUP_COUNT,
        ),
        logging.StreamHandler(sys.stdout),
    ],
)
