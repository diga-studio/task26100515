# task26100515
# evergreen-calendar

常青内容轮转日历库（纯 Python + SQLite，无外部服务依赖）。运营团队登记
7/14/30 天间隔的轮转计划，临时取消或移动单次发布，并按 UTC 窗口查询
发布总量与最早的前 100 条安排。本库只维护日历，不执行实际发布。

## 安装与测试

仅需 Python 3.9+（标准库 `zoneinfo` + `sqlite3`）。运行测试：

```bash
python3 -m pytest tests/ -q
```

## 快速上手

```python
from datetime import date, datetime, time, timezone
from evergreen_calendar import EvergreenCalendar

cal = EvergreenCalendar("calendar.db")   # SQLite 持久化，可重复打开

# 1. 登记轮转计划（登记后不可修改；间隔只能是 7/14/30 天）
cal.register_plan(
    plan_id="weekly-blog",
    start_date=date(2024, 3, 3),        # 起始当地日期（包含）
    publish_time=time(2, 30),           # America/Chicago 当地钟点
    interval_days=7,                    # 7 / 14 / 30 个当地日历日
    end_date=None,                      # 可选；结束日不包含，None 表示持续轮转
)

# 2. 调整单次发布（计划标识 + 原计划当地日期定位；以最后一次调整为准）
cal.cancel_occurrence("weekly-blog", date(2024, 3, 17))
cal.reschedule_occurrence(
    "weekly-blog", date(2024, 3, 24),
    datetime(2024, 3, 25, 13, 0, tzinfo=timezone.utc),   # 目标为 UTC 时刻
)

# 3. 查询日历（UTC 窗口，左闭右开）
result = cal.query(
    datetime(2024, 3, 1, tzinfo=timezone.utc),
    datetime(2124, 3, 1, tzinfo=timezone.utc),           # 跨度可达 100 年
)
print(result.total_count)                # 窗口内未取消发布的准确总数
for item in result.items:                # 按发布时间最早的前 100 条
    print(item.plan_id, item.orig_date, item.publish_utc)

cal.close()                              # 关闭后数据保留，重开结果一致
```

## 语义说明

- **时区**：所有计划使用 `America/Chicago`。春季跳时不存在的钟点顺延跳过
  的时长（2024-03-10 02:30 → 当天 UTC 08:30）；秋季回拨出现两次的钟点取
  较早的 UTC 时刻（2024-11-03 01:30 → 当天 UTC 06:30）。这些调整不改变
  后续发布的计划日期与钟点，30 天间隔不等于一个月。
- **单次调整**：取消或改到指定 UTC 时刻；再次调整同一次发布以最后一次
  为准（包括把已取消的重新改期）。改期不产生额外发布、不牵动后续轮转，
  可跨越查询窗口与计划结束日期。对原计划中不存在的日期（如不命中轮转
  周期或超出结束日期）的调整不生效。
- **查询**：返回窗口内未取消发布的准确总数，以及按实际发布时间最早的
  前 100 条；时间相同时按计划标识字典序、原计划日期升序排列。被改入
  窗口的计入，被移出/取消的排除；多条发布改到同一时刻分别保留。

## 性能

计数基于等差数列算术（芝加哥 UTC 偏移始终在 [-6h, -5h]，窗口边界 1 小时
带内每个计划至多一次发布需要精确换算），前 100 条通过堆归并生成，因此
查询耗时与窗口跨度无关。实测（见 `tests/test_performance.py`）：

- 规模：20,000 条计划 + 100,000 条单次调整，窗口 100 年
  （窗口内约 4,300 万次发布）
- 单次查询：约 1.8 s（限制 3 s）
- 查询峰值内存：约 52 MiB（限制 256 MiB）
- 数据库磁盘占用：约 9.5 MiB（限制 128 MiB）

## 项目结构

- `evergreen_calendar/models.py` — 数据类型（计划、调整、发布、查询结果）
- `evergreen_calendar/timeutil.py` — Chicago 当地时间 → UTC 的 DST 转换
- `evergreen_calendar/storage.py` — SQLite 持久化（计划表 + 调整表）
- `evergreen_calendar/core.py` — 公开 API 与查询算法
- `tests/` — DST 边界、单次调整、查询语义、性能与重开一致性测试
