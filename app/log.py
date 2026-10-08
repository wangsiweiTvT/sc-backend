# 日志模块（loguru）：统一「时间 | 级别 | 位置 | 消息」格式，文件超 10MB 自动轮转、旧文件保留 7 天
# 用法：各服务入口调用 setup("名字") 绑定 logs/名字.log；业务代码直接 log("...")（print 风格壳）
import inspect
import logging

from loguru import logger


def setup(name):
    """绑定本服务的日志文件 logs/{name}.log（10MB 轮转 / 保留 7 天），并去掉默认的控制台输出"""
    logger.remove()
    logger.add(
        f"logs/{name}.log",
        rotation="10 MB",
        retention="7 days",
        encoding="utf-8",  # 中文/emoji
    )


def log(*args, **kwargs):
    """print 风格兼容壳：消息走 INFO，位置显示真实调用行（flush 等旧参数自动忽略）"""
    logger.opt(depth=1).info(" ".join(str(a) for a in args))


class _InterceptHandler(logging.Handler):
    """把标准库 logging 的记录转投给 loguru（uvicorn 的日志走的是标准库）"""

    def emit(self, record):
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno
        # 沿调用栈跳过 logging 模块自身的帧，让 loguru 显示真实来源
        frame, depth = inspect.currentframe(), 0
        while frame and (depth == 0 or frame.f_code.co_filename == logging.__file__):
            frame = frame.f_back
            depth += 1
        logger.opt(depth=depth, exception=record.exc_info).log(level, record.getMessage())


def intercept_stdlib():
    """接管 uvicorn / uvicorn.access 等标准库日志，让它们也写进本服务的 loguru 文件"""
    logging.basicConfig(handlers=[_InterceptHandler()], level=0, force=True)
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        std_logger = logging.getLogger(name)
        std_logger.handlers = [_InterceptHandler()]
        std_logger.propagate = False
