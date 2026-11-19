# -*- coding: utf-8 -*-
"""
数据库字段巡检回填定时任务
每天 06:30 和 07:00 各执行一次：
  - db_daily_decision_test 表的 geo_desc / geo_stance / geo_summary 字段
  - db_daily_report_page 表的 geopolitical 字段
若“当日”行存在且目标字段为空，则用最近一个早于当日的行（即前一天的数据）填充；
若“当日”行不存在，则用前一天数据新建一行（仅复制目标字段）。
"""

import os
import time
import logging
import datetime

import pymysql
from dotenv import load_dotenv

load_dotenv()  # 读取 .env 文件中的环境变量（已存在的环境变量优先，不覆盖）

# ---------------- 配置 ----------------
DB_HOST = os.getenv("DB_HOST", "host.docker.internal")
DB_PORT = int(os.getenv("DB_PORT", "3307"))
DB_USER = os.getenv("DB_USER", "root")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")
DB_NAME = os.getenv("DB_NAME", "db_singapore")

RUN_ON_STARTUP = os.getenv("RUN_ON_STARTUP", "false").lower() == "true"

CHECK_HOURS = ((6, 30), (7, 0))  # 每天两次检查时间点（容器本地时间，配合 TZ 环境变量）

# 目标字段定义：(表名, 日期字段, [需要检查的字段])
TARGETS = [
    ("db_daily_decision_test", "decision_date",
     ["geo_desc", "geo_stance", "geo_summary"]),
    ("db_daily_report_page", "report_date",
     ["geopolitical"]),
]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("geo-fill-job")


def is_empty(value) -> bool:
    """判断字段值是否为空：NULL / 空串 / JSON 的 'null' 视为空。"""
    if value is None:
        return True
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8", "ignore")
    if isinstance(value, str):
        v = value.strip()
        return v == "" or v.lower() == "null"
    return False


def get_conn():
    return pymysql.connect(
        host=DB_HOST, port=DB_PORT, user=DB_USER, password=DB_PASSWORD,
        database=DB_NAME, charset="utf8mb4", autocommit=False,
    )


def check_and_fill(conn, target_date: datetime.date = None) -> None:
    """检查并回填指定日期的字段；默认检查今天。target_date 仅供手动测试指定任意日期。"""
    today = target_date or datetime.date.today()
    cursor = conn.cursor(pymysql.cursors.DictCursor)
    try:
        for table, date_col, fields in TARGETS:
            # 当日行
            cursor.execute(
                f"SELECT * FROM `{table}` WHERE `{date_col}` = %s", (today,)
            )
            today_row = cursor.fetchone()

            # 最近一个早于当日的行（即“前一天”的数据，可兼容周末/假日断档）
            cursor.execute(
                f"SELECT * FROM `{table}` WHERE `{date_col}` < %s "
                f"ORDER BY `{date_col}` DESC LIMIT 1", (today,)
            )
            prev_row = cursor.fetchone()

            if prev_row is None:
                log.warning("[%s] 没有找到 %s 之前的数据，跳过", table, today)
                continue

            if today_row is None:
                # 当日行不存在：用前一天目标字段新建一行
                values = [prev_row[f] for f in fields]
                if all(is_empty(v) for v in values):
                    log.info("[%s] 当日无记录且前一天目标字段也为空，无需更新！", table)
                    continue
                cols = ", ".join(f"`{c}`" for c in [date_col] + fields)
                placeholders = ", ".join(["%s"] * (len(fields) + 1))
                cursor.execute(
                    f"INSERT INTO `{table}` ({cols}) VALUES ({placeholders})",
                    [today] + values,
                )
                log.info("[%s] 当日无记录，已用 %s 的数据新建 %s 行，已完成！",
                         table, prev_row[date_col], today)
            else:
                # 当日行存在：逐个检查目标字段，空的用前一天数据回填
                sets, params = [], []
                for f in fields:
                    if is_empty(today_row[f]) and not is_empty(prev_row[f]):
                        sets.append(f"`{f}` = %s")
                        params.append(prev_row[f])
                if not sets:
                    log.info("[%s] %s 目标字段有值，无需更新！", table, today)
                    continue
                params.append(today_row["id"])
                cursor.execute(
                    f"UPDATE `{table}` SET {', '.join(sets)} WHERE `id` = %s",
                    params,
                )
                log.info("[%s] 已回填 %s 行字段: %s（来源 %s），已完成！",
                         table, today, ", ".join(sets_keys(sets)), prev_row[date_col])
        conn.commit()
        log.info("本次检查完成")
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()


def sets_keys(sets):
    return [s.split("`")[1] for s in sets]


def next_run_time(now: datetime.datetime) -> datetime.datetime:
    """计算下一次 06:30 / 07:00 的执行时间。"""
    candidates = []
    for h, m in CHECK_HOURS:
        candidates.append(now.replace(hour=h, minute=m, second=0, microsecond=0))
    for c in candidates:
        if c > now:
            return c
    return candidates[0] + datetime.timedelta(days=1)


def main() -> None:
    log.info("任务启动，检查时间点: %s，数据库 %s:%s/%s",
             CHECK_HOURS, DB_HOST, DB_PORT, DB_NAME)
    if RUN_ON_STARTUP:
        log.info("启动即执行一次检查")
        with get_conn() as conn:
            check_and_fill(conn)
    while True:
        now = datetime.datetime.now()
        nxt = next_run_time(now)
        wait = (nxt - now).total_seconds()
        log.info("下次执行时间: %s（%.0f 秒后）", nxt, wait)
        time.sleep(wait)
        try:
            with get_conn() as conn:
                check_and_fill(conn)
        except Exception as exc:
            log.exception("本次检查失败，等待下一个时间点: %s", exc)


if __name__ == "__main__":
    main()
