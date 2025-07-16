# utils.py

import logging
from pathlib import Path

def setup_logging(logfile: Path = None):
    """
    Configures root logger. If logfile is given, logs to that file;
    otherwise logs to stdout.
    """
    fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    if logfile:
        logging.basicConfig(
            filename=str(logfile),
            level=logging.INFO,
            format=fmt
        )
    else:
        logging.basicConfig(
            level=logging.INFO,
            format=fmt
        )
