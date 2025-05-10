import logging
import sys


def setup_logging(name: str) -> logging.Logger:
    # Create a root logger
    root_logger = logging.getLogger(name)
    root_logger.setLevel(logging.INFO)

    console_handler = logging.StreamHandler(stream=sys.stdout)
    console_handler.setLevel(logging.INFO)
    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )

    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    return root_logger
