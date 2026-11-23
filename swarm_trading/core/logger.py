import logging
import sys
from typing import Optional


class StructuredLogger:
    """Thin wrapper that appends key=value pairs. Output goes through the root logger."""

    def __init__(self, name: str, log_level: str = "INFO"):
        self.logger = logging.getLogger(name)
        self.logger.setLevel(getattr(logging, log_level.upper()))

    def debug(self, message: str, **kwargs) -> None:
        self._log(logging.DEBUG, message, **kwargs)

    def info(self, message: str, **kwargs) -> None:
        self._log(logging.INFO, message, **kwargs)

    def warning(self, message: str, **kwargs) -> None:
        self._log(logging.WARNING, message, **kwargs)

    def error(self, message: str, **kwargs) -> None:
        self._log(logging.ERROR, message, **kwargs)

    def _log(self, level: int, message: str, **kwargs) -> None:
        if kwargs:
            extra_info = " | ".join(f"{k}={v}" for k, v in kwargs.items())
            message = f"{message} | {extra_info}"
        self.logger.log(level, message)


def setup_logging(log_level: str = "INFO") -> None:
    """Configure the root logger once, to stdout. No duplicate handlers."""
    root = logging.getLogger()
    root.setLevel(getattr(logging, log_level.upper()))
    if not any(getattr(h, "_swarm_handler", False) for h in root.handlers):
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(
            logging.Formatter(
                fmt="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )
        handler._swarm_handler = True
        root.addHandler(handler)
