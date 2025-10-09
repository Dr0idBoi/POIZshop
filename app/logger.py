# app/logger.py
import logging
import logging.handlers
import sys
from pathlib import Path
from typing import Optional

from .config import settings

def setup_logging(log_dir: Optional[Path] = None) -> None:
    """
    Setup application logging with both file and console handlers
    
    Args:
        log_dir: Optional directory for log files. Defaults to 'logs' in current directory.
    """
    try:
        # Create logs directory if needed
        log_dir = log_dir or Path("logs")
        log_dir.mkdir(parents=True, exist_ok=True)
        
        # Get root logger
        root_logger = logging.getLogger()
        root_logger.setLevel(settings.log_level)
        
        # Remove existing handlers to avoid duplicates
        for handler in root_logger.handlers[:]:
            root_logger.removeHandler(handler)
        
        # Configure formatters
        file_formatter = logging.Formatter(
            '%(asctime)s [%(levelname)s] %(name)s: %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        console_formatter = logging.Formatter(
            '%(asctime)s [%(levelname)s] %(name)s: %(message)s',
            datefmt='%H:%M:%S'
        )
        
        # File handler for all logs
        main_handler = logging.handlers.RotatingFileHandler(
            log_dir / "app.log",
            maxBytes=10_485_760,  # 10MB
            backupCount=5,
            encoding='utf-8'
        )
        main_handler.setFormatter(file_formatter)
        main_handler.setLevel(settings.log_level)
        root_logger.addHandler(main_handler)
        
        # Separate file handler for errors
        error_handler = logging.handlers.RotatingFileHandler(
            log_dir / "error.log",
            maxBytes=10_485_760,  # 10MB
            backupCount=5,
            encoding='utf-8'
        )
        error_handler.setFormatter(file_formatter)
        error_handler.setLevel(logging.ERROR)
        root_logger.addHandler(error_handler)
        
        # Console handler
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(console_formatter)
        console_handler.setLevel(settings.log_level)
        root_logger.addHandler(console_handler)
        
        # Set specific levels for some loggers
        logging.getLogger("aiosqlite").setLevel(logging.WARNING)
        logging.getLogger("aiogram").setLevel(logging.INFO)
        logging.getLogger("apscheduler").setLevel(logging.INFO)
        
        # Log startup information
        root_logger.info("Logging system initialized")
        root_logger.info(f"Log level: {settings.log_level}")
        root_logger.info(f"Log directory: {log_dir.absolute()}")
        
    except Exception as e:
        print(f"Failed to setup logging: {e}", file=sys.stderr)
        raise

class LoggerAdapter(logging.LoggerAdapter):
    """Custom logger adapter to add context information to log messages"""
    
    def process(self, msg, kwargs):
        # Add user_id if available
        if 'user_id' in self.extra:
            msg = f"[User {self.extra['user_id']}] {msg}"
        
        # Add request_id if available
        if 'request_id' in self.extra:
            msg = f"[Request {self.extra['request_id']}] {msg}"
            
        return msg, kwargs

def get_logger(name: str, **extra) -> LoggerAdapter:
    """
    Get a logger with optional extra context
    
    Args:
        name: Logger name
        **extra: Extra context to add to log messages
    
    Returns:
        LoggerAdapter instance
    """
    logger = logging.getLogger(name)
    return LoggerAdapter(logger, extra)
