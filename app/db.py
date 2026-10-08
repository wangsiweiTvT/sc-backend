# 数据库公共配置：consumer / api / detector 共用，改连接信息只改这一处
# 密码不写死在代码里：优先读环境变量，否则读仓库根目录 .env（.gitignore 已排除，不会进仓库）
import os
import pymysql


def _load_env():
    """读取仓库根目录的 .env（每行 KEY=VALUE），只填充尚未设置的环境变量"""
    # app/ 的上一级 = 仓库根目录（.env 固定放根目录）
    env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
    if not os.path.exists(env_path):
        return
    with open(env_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())


_load_env()

if not os.environ.get("DB_PASSWORD"):
    raise RuntimeError("缺少数据库密码：请复制 .env.example 为 .env，填入 DB_PASSWORD 后重试")

DB_CONFIG = {
    "host": os.environ.get("DB_HOST", "127.0.0.1"),
    "port": int(os.environ.get("DB_PORT", "3306")),
    "user": os.environ.get("DB_USER", "root"),
    "password": os.environ.get("DB_PASSWORD"),
    "database": os.environ.get("DB_NAME", "mqtt_data"),
    "autocommit": True,
}

# 复用的数据库连接
db_conn = None

def get_db_conn():
    """获取数据库连接，断线自动重连"""
    global db_conn
    try:
        db_conn.ping(reconnect=True)
    except Exception:
        db_conn = pymysql.connect(**DB_CONFIG)
    return db_conn
