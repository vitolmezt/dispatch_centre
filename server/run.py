# -*- coding: utf-8 -*-
"""启动调度中心后端服务."""
import logging
import uvicorn


class _ColorFormatter(logging.Formatter):
    """带 ANSI 颜色的 Formatter：levelname 与 message 按级别配色.

    仅当 stdout 是终端时启用彩色；重定向到文件/管道时自动退回纯文本。
    """

    _LEVEL_COLORS = {
        "DEBUG": "36",     # 青
        "INFO": "32",      # 绿
        "WARNING": "33",   # 黄
        "ERROR": "31",     # 红
        "CRITICAL": "35",  # 品红
    }
    _RESET = "\033[0m"

    def __init__(self, fmt, datefmt=None):
        super().__init__(fmt=fmt, datefmt=datefmt)
        import sys
        self._enabled = hasattr(sys.stdout, "isatty") and sys.stdout.isatty()

    def format(self, record):
        if not self._enabled:
            return super().format(record)
        code = self._LEVEL_COLORS.get(record.levelname)
        color = f"\033[{code}m" if code else ""
        orig_lv = record.levelname
        # 先按原格式算出纯文本消息（这会消费 record.args）
        try:
            orig_msg = record.getMessage()
        except Exception:
            orig_msg = record.msg
        record.levelname = f"{color}{orig_lv}{self._RESET}"
        record.msg = f"{color}{orig_msg}{self._RESET}"
        # 关键：清空 args，避免 super().format() 再次对已填色的 msg 做 % 运算
        record.args = ()
        try:
            return super().format(record)
        finally:
            record.levelname, record.msg = orig_lv, orig_msg


if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
        log_level="info",
        access_log=True,
        log_config={
            # 访问/默认日志加时间戳 + ANSI 颜色（仅终端彩色）。
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "access": {
                    "()": _ColorFormatter,
                    "fmt": "%(asctime)s %(message)s",
                    "datefmt": "%Y-%m-%d %H:%M:%S",
                },
                "default": {
                    "()": _ColorFormatter,
                    "fmt": "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
                    "datefmt": "%Y-%m-%d %H:%M:%S",
                },
            },
            "handlers": {
                "default": {"formatter": "default", "class": "logging.StreamHandler"},
                "access": {"formatter": "access", "class": "logging.StreamHandler"},
            },
            "loggers": {
                "uvicorn": {"handlers": ["default"], "level": "INFO", "propagate": False},
                "uvicorn.error": {"handlers": ["default"], "level": "INFO", "propagate": False},
                "uvicorn.access": {"handlers": ["access"], "level": "INFO", "propagate": False},
            },
        },
    )