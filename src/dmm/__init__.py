import os
import sys
import logging
from logging.handlers import RotatingFileHandler, WatchedFileHandler

# Keep the log on disk bounded and split into chunks so it stays greppable.
# Overridable via env without touching code.
LOG_FILE = os.environ.get("DMM_LOG_FILE", "dmm.log")
_LOG_MAX_BYTES = int(os.environ.get("DMM_LOG_MAX_BYTES", 10 * 1024 * 1024))  # 10 MB per chunk
LOG_BACKUP_COUNT = int(os.environ.get("DMM_LOG_BACKUP_COUNT", 10))           # keep 10 old chunks

logging.basicConfig(
    format="(%(threadName)s) [%(asctime)s] %(levelname)s: %(message)s",
    datefmt="%m-%d-%Y %H:%M:%S %p",
    level=logging.INFO,  # DMM() applies [dmm] log_level once the config is loaded
    handlers=[
        RotatingFileHandler(
            filename=LOG_FILE,
            maxBytes=_LOG_MAX_BYTES,
            backupCount=LOG_BACKUP_COUNT,
        ),
        logging.StreamHandler(sys.stdout),
    ],
)


def follow_log_file_in_child():
    """Swap the inherited rotating handler for one that only follows the file.

    A forked process (the frontend) inherits the parent's RotatingFileHandler
    and its open file. When the parent rotates, the child keeps writing to the
    renamed file, so its lines drift down dmm.log.1, .2, ... out of time order
    and are deleted with the last backup. Only the parent rotates; the child
    reopens dmm.log whenever the file it holds is moved away.
    """
    root = logging.getLogger()
    for handler in list(root.handlers):
        if isinstance(handler, RotatingFileHandler):
            root.removeHandler(handler)
            # Not close(): the parent's handler shares the file description.
            follower = WatchedFileHandler(handler.baseFilename)
            follower.setFormatter(handler.formatter)
            follower.setLevel(handler.level)
            root.addHandler(follower)
