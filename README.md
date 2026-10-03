# 常青内容轮转日历库

运营团队用本库登记常青内容的轮转发布计划、临时调整单次发布，并查询任意
UTC 时间范围内的发布安排。所有计划使用 `America/Chicago` 时区；本库只维护
日历，不执行实际发布。计划与调整持久化在 SQLite 中，关闭重开后保留。

## 环境

- Python 3.11+（仅标准库：`sqlite3`、`zoneinfo`）
- 系统需有 IANA 时区数据（`America/Chicago`）；无数据库等外部服务

## 快速开始

```python
from datetime import date, datetime, time, timezone
from evergreen_calendar import EvergreenCalendar

cal = EvergreenCalendar("calendar.sqlite")   # 打开或创建库文件

# 1. 登记轮转计划（登记后不可修改；interval 只能是 7 / 14 / 30）
cal.register_plan(
    plan_id="weekly-blog",
    start_date=date(2024, 1, 1),        # 起始当地日期（包含）
    publish_time=time(9, 30),           # 芝加哥当地钟点
    interval_days=7,                    # 7 / 14 / 30 个当地日历日
    end_date=None,                      # 结束当地日期（不包含），None 表示持续轮转
)

# 2. 调整单次发布（由 计划标识 + 原计划当地日期 识别；以最后一次调整为准）
cal.cancel_publish("weekly-blog", date(2024, 1, 8))
cal.reschedule_publish(                  # 已取消的也可重新改期
    "weekly-blog", date(2024, 1, 8),
    datetime(2024, 1, 9, 15, 0, tzinfo=timezone.utc),   # 目标为 UTC 时刻
)

# 3. 查询日历：UTC 窗口，左闭右开
result = cal.query(
    datetime(2024, 1, 1, tzinfo=timezone.utc),
    datetime(2124, 1, 1, tzinfo=timezone.utc),          # 跨度可达 100 年
)
result.total        # 窗口内未取消发布的准确总数
result.items        # 按实际发布时间最早的前 100 条（不足则全部）
for item in result.items:
    item.plan_id         # 计划标识
    item.original_date   # 原计划当地日期（发布身份）
    item.publish_at      # 实际发布 UTC 时刻

cal.close()         # 提交并关闭；重开后数据保留
```

## 语义说明

- **轮转展开**：各次发布的当地日期 = 起始日 + k × 间隔，钟点不变；
  30 天不等于一个月。起始日包含、结束日不包含。
- **夏令时**：当地钟点在春季跳时不存在时，顺延跳过的时长
  （如 2024-03-10 02:30 → 当天 UTC 08:30）；秋季回拨出现两次时取较早的
  UTC 时刻（如 2024-11-03 01:30 → 当天 UTC 06:30）。不影响后续轮转的
  计划日期与钟点。
- **单次调整**：取消或改到指定 UTC 时刻；重复调整同一次发布时以最后一次
  为准。改期不产生额外发布、不牵动后续轮转，可跨越查询窗口与计划结束
  日期；结束日期只决定原计划中是否存在这次发布（不存在的调整无效果）。
- **查询**：窗口内被改出的排除、窗口外被改入的计入且只计一次；不同发布
  改到同一时刻仍分别保留。排序按（实际时刻, 计划标识字典序, 原计划日期）
  升序。

## 运行测试

```bash
python3 -m pytest tests/ -v
```

`tests/test_performance.py` 会在子进程中构建 20000 条计划 + 100000 条
调整，查询 100 年窗口，并用基于 `zoneinfo` 的独立参照实现校验总数与前
100 条，同时校验重开一致性与性能指标（本机实测：单次查询约 0.44s，
峰值内存约 59 MiB，数据库约 2.8 MiB）。

## 实现要点

- `evergreen_calendar/tzutil.py`：预扫描时区偏移跳变表，当地时刻 → UTC
  只做二分查找与少量日期运算。
- `evergreen_calendar/calendar.py`：同一计划内各次发布的 UTC 时刻随序号
  严格递增（间隔 ≥ 7 天，夏令时偏移 ≤ 1 小时），窗口计数按此二分查找，
  无需展开全部日历；前 100 条用堆归并各计划窗口内的有序序列并混入被
  改入窗口的发布。
- `evergreen_calendar/store.py`：SQLite 持久化，写入批量提交、关闭时提交。
