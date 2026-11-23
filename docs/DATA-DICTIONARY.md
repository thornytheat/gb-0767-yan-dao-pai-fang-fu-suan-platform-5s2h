# 烟道监测记录定义

## 原件

| 文件 | 字段与约束 |
|---|---|
| window.json | `stack` 烟道字符串；`start,end,cutoff` 带偏移整分钟；`start,end` 对齐 UTC 整点且跨度 1–24 小时；`cutoff≥end`；`reference` 参比氧百分比字符串 0–20；`threshold` 有效分钟整数 1–60；`tolerance` mg/Nm³ 非负 Decimal 字符串 |
| minutes.csv | `stack,timestamp,value,unit,basis,moisture,oxygen,status,revision,received`。浓度非负，单位 mg/Nm3、g/Nm3、ug/Nm3 或 µg/Nm3；干湿基 `dry/wet`，湿基必填湿度百分比；氧量百分比 0–20；仪器仅 `OK` 有效；修订号非负整数；收件时刻不得早于测量 |
| modes.csv | `stack,start,end,mode`。带偏移整分钟区间，`STARTUP` 或 `SHUTDOWN` |
| reports.csv | `stack,hour,value,ticket`。UTC 整点、参比干基浓度 mg/Nm³、原单据号字符串 |
| receipts.csv | `stack,hour,receipt,event,value,ticket,received`。监管端对已受理报送单据的回执：`event` 为 `ACCEPT`（更正受理，必带非负新值）或 `WITHDRAW`（撤回受理，值留空或缺值记号）；`receipt` 为回执号、`ticket` 关联原报送单据、`received` 为监管收件时刻。可省略，省略时按无回执对账 |

CSV 用 UTF-8，可带 BOM；列顺序可变。引号内换行保留，`line` 是记录起始原件行。无内容报 `EMPTY_FILE`，仅表头返回 `header_only`，只有空行或坏行返回 `no_usable_rows` 并隔离。输入记录 = 可读 + 重复表头 + 结构隔离。`-`、`N/A`、`NULL`、`未知` 与空字段均缺值；`null` 为 JSON 缺值，零是有效读数。单文件文本上限 400,000 字符，请求上限 8 MiB。

## 分级裁决

1. 烟道与 UTC 分钟为业务键，修订只认收件截止以内的版本。最大修订出现不同测量原值时整组不采信，无须回退旧版。相同内容择最早收件及最小原件行；收件间隔大于 5 分钟标迟补。`AFTER_CUTOFF`、`SUPERSEDED`、`DUPLICATE` 和 `CONFLICT` 的原件可在折算隔离表查询。
2. 先以单位系数转 mg/Nm³。湿基以 `浓度 × 100 / (100 − 湿度)` 转干基；干基原值直接用。参比浓度 = `干基 × (21 − 参比氧) / (21 − 实测氧)`。数学计算使用 40 位 Decimal。
3. 窗口和工况统一为 `[start,end)`。启停并集免计，重叠保留两个来源，`SHUTDOWN` 优先展示。剩余分钟按异常状态、缺测、冲突、数值失效分别记为缺证。应测 = 有效 + 免计 + 缺证。
4. 每小时以完整精度求有效分钟算术平均，达门槛后以 `ROUND_HALF_UP` 公布两位小数；门槛固定，整小时免计时单列。时刻按 UTC 分桶，页面展示 UTC+08:00。
5. 报送小时冲突按不同 Decimal 原值识别。可比值均显示两位，差额 = 复算 − 报送，绝对差额 ≤ 容差为一致。窗外报送单列，缺数与零分开。
6. 回执只在 `received ≤ cutoff` 时采信，晚到回执不改变本批次差额，交班仍按已受理旧单对账，避免误报。同一回执号的重试件只登记一次，其余隔离为 `DUPLICATE`；同号内容不一致整组隔离为 `RECEIPT_CONFLICT`。`ACCEPT` 把单据采用值改为回执新值，`WITHDRAW` 使单据在对账中失效（小时回到 `NO_REPORT`）。回执找不到单据记 `UNKNOWN_TICKET`，单据号存在但小时不符记 `RECEIPT_HOUR_MISMATCH`。对账页逐小时交代差额采用的单据号、原件行、回执号与受理/撤回事件。
7. 晚到回执补齐或监管端撤回受理后，以同一小时原件加新回执重新归档，得到新运行编号与新差额；旧批次的原件、回执缺位和当时差额随证据包不可变留存，已签发证据仍可核验当时采用的单据。

## 本地边界

只核验单烟道单污染物的浓度与小时有效性。流量、排放总量、合规限值判定及政策专有豁免由外部业务流程处理。按固定 UTC+08:00 显示，数据源必须提供偏移。
