"""FB Engagement Assistant — Entry point."""

import logging
from pathlib import Path

from dotenv import load_dotenv

from bot.modules.logger import setup_logging


def main():
    """Initialize and start the bot system."""
    load_dotenv()
    setup_logging()

    logger = logging.getLogger(__name__)
    logger.info("FB Engagement Assistant starting...")

    # TODO: Initialize database
    # TODO: Load configs
    # TODO: Start Celery worker/beat
    # TODO: Initialize Sentry

    logger.info("System ready.")


if __name__ == "__main__":
    main()
