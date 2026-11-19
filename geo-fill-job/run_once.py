# -*- coding: utf-8 -*-
"""
手动测试脚本：执行一次字段检查/回填后立即退出，不进入定时循环。
用法：
  python run_once.py              # 检查“今天”
  python run_once.py 2026-11-18   # 检查指定日期（测试任意一天）
"""

import sys
import datetime

from app import get_conn, check_and_fill, log


def main() -> int:
    target = None
    if len(sys.argv) > 1:
        try:
            target = datetime.date.fromisoformat(sys.argv[1])
        except ValueError:
            log.error("日期格式错误，应为 YYYY-MM-DD，例如: python run_once.py 2026-11-18")
            return 1

    try:
        with get_conn() as conn:
            check_and_fill(conn, target)
    except Exception as exc:
        log.exception("检查执行失败: %s", exc)
        return 1

    log.info("手动检查执行完毕")
    return 0


if __name__ == "__main__":
    sys.exit(main())
