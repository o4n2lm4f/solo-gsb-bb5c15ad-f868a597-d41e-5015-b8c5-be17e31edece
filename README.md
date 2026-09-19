# 样本交接链路审计（Sample Chain-of-Custody Audit）

基于 **Python FastAPI + SQLite + 原生 HTML/CSS/JavaScript** 的样本交接链路审计网页。
为每个样本录入带有**时间区间**与**经手人**的交接事件后，系统按起始时间排序，
自动校验保管链路是否存在：

- 🔴 **保管断档（gap）**：上一任归还后、下一任接手前，存在无人负责的时间段；
- 🔴 **交接重叠（overlap）**：两个（含不相邻的）经手人在同一时间段同时持有样本，
  或上一任尚未归还下一任即接手；
- 🟠 **逆序录入（out-of-order）**：录入顺序（seq）与交接时间顺序不一致（事后补录）；
- 🔴 **起止倒置（reversed interval）**：单条事件结束时间早于起始时间。

时间线上以泳道条带展示各段保管区间，并叠加高亮断档（紫色斜纹）与重叠（红色）区域；
同时给出统计数字与**可读的中文审计结论**。支持编辑/删除事件，以及导出当前样本的
**Markdown / HTML / JSON** 审计报告。

审计完成后，审计员可对当前结论执行**结论签署**：填写签署人、签署时间与备注后，
系统保存一份包含样本信息、交接事件与审计发现的**不可变快照**（附 SHA-256 校验值）。
此后事件的任何增删改都不会改写历史快照；快照支持列表查看、详情核对与**撤销签署**
（撤销必须填写原因，原快照保留，仅标记状态）。导出的审计报告会自动附上签署记录。

链路中的每对相邻交接事件还可执行**双向交接确认**：系统按审计排序（起始时间）自动
生成交接项，交出方与接收方分别填写姓名与确认时间。两侧都确认且**上一段结束时间等于
下一段开始时间**时标记为「已确认」；两侧已确认但时间不相等（断档 / 重叠 / 未归还）
显示「时间不一致」，否则为「待确认」。每条确认绑定两条事件当时的关键字段摘要
（SHA-256），任一事件随后被编辑或删除时，原确认保留为**失效记录**（含确认时快照与
失效原因），不计入有效状态，需重新确认。导出报告会列出各交接项状态与失效记录。

## 快速开始（Docker Compose，推荐）

```bash
docker compose up -d --build
```

启动后访问固定端口：

> http://localhost:8080

容器启动时会自动完成：

1. 安装 Python 依赖（镜像构建阶段）；
2. 初始化 SQLite（建表，写入 2 个演示样本，数据文件位于 `/data/audit.db`，
   通过命名卷 `audit-data` 持久化）；
3. 启动 uvicorn 监听 `8080`。

停止 / 清理：

```bash
docker compose down          # 停止并删除容器（保留数据卷）
docker compose down -v       # 同时删除数据库卷
```

## 本地开发运行（不用 Docker）

需要 Python 3.11+：

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt

uvicorn app.main:app --host 0.0.0.0 --port 8080 --reload
```

数据库默认创建在 `app/audit.db`（首次启动自动建表并写入演示数据）。
可通过环境变量 `AUDIT_DB_PATH` 自定义路径。

运行测试：

```bash
pytest -q
```

## 使用说明

1. 点击右上角 **＋ 新建样本**（编号唯一），或从左侧选择演示样本；
2. 在「交接事件」卡片填写经手人、起始时间、结束时间（**留空表示当前仍在保管中**）、备注，
   点击 **登记交接事件**；
3. 审计结论、统计与时间线实时刷新。时间条按起始时间排列，冲突事件标红，
   点击下方「审计发现」条目可高亮对应的时间条与表格行；
4. 点击事件行的 **编辑** 可修改任意字段（录入序 seq 保持不变，便于追溯补录行为），
   保存后重新审计；也可删除事件；
5. 在「双向交接确认」卡片对每个交接项分别填写**交出方 / 接收方**确认人姓名与确认时间
   （默认当前时间）：两侧都确认为「已确认」；若任一事件之后被编辑或删除，原确认自动
   转为失效记录并展示在下方表格中，需对当前交接项重新确认；
6. 在「结论签署」卡片填写签署人、签署时间（默认当前时间）与备注，点击
   **签署当前审计结论** 生成不可变快照；列表中点击 **详情 / 撤销** 可查看快照完整内容
   （含完整性校验），有效签署可在详情弹窗中填写原因后撤销，撤销后原快照保留；
7. 通过「交接事件」卡片右上角的 **导出审计报告** 下载 Markdown / HTML / JSON 三种格式之一，
   报告自动附上各交接项确认状态（含失效记录）与全部签署记录（含状态与撤销原因）。

演示样本 `SAM-DEMO-001` 预置了断档、重叠与逆序补录，`SAM-DEMO-002` 是无缝链路，
开箱即可对比两种审计结果。

## 审计规则说明

| 规则 | 判定方式 | 级别 |
| --- | --- | --- |
| 断档 gap | 按起始时间排序后，相邻两段 `cur.start > prev.end`，差值即断档时长 | 冲突（审计不通过） |
| 重叠 overlap | 相邻段 `cur.start < prev.end`；开放保管（无结束时间）之后又出现新段；以及任意两段非相邻区间的时间交叉 | 冲突 |
| 起止倒置 | 单条事件 `end < start` | 冲突（该条不参与区间链计算） |
| 逆序录入 | 时间更早的事件录入序 `seq` 反而更大 | 告警（链路本身可通过） |

结论分三档：`pass`（无缝衔接）、`warning`（链路连续但存在逆序补录）、
`fail`（存在断档/重叠/倒置）。

## API 一览

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/health` | 健康检查 |
| GET | `/api/samples` | 样本列表（含事件数） |
| POST | `/api/samples` | 新建样本 |
| GET | `/api/samples/{id}` | 样本详情 + 事件 + 审计结果 + 签署快照摘要 |
| DELETE | `/api/samples/{id}` | 删除样本（级联删除事件与签署快照） |
| POST | `/api/samples/{id}/events` | 登记交接事件（自动分配录入序） |
| PUT | `/api/events/{id}` | 编辑交接事件 |
| DELETE | `/api/events/{id}` | 删除交接事件 |
| GET | `/api/samples/{id}/audit` | 仅获取审计结果 |
| GET | `/api/samples/{id}/handovers` | 交接项列表（确认状态 + 失效记录 + 统计） |
| POST | `/api/samples/{id}/handovers/confirm` | 双向交接确认：确认交接项某一侧（side=out/in） |
| POST | `/api/samples/{id}/signoffs` | 结论签署：保存当前审计结果的不可变快照 |
| GET | `/api/samples/{id}/signoffs` | 签署快照列表（状态 + 审计摘要） |
| GET | `/api/signoffs/{id}` | 快照详情（完整快照 + 完整性校验） |
| POST | `/api/signoffs/{id}/revoke` | 撤销签署（需原因，原快照保留） |
| GET | `/api/samples/{id}/report?format=markdown\|html\|json` | 导出审计报告（带下载文件名，含交接确认状态与签署记录） |

交互式 API 文档：http://localhost:8080/docs

## 目录结构

```
.
├── app/
│   ├── main.py            # FastAPI 路由与静态文件挂载
│   ├── audit.py           # 审计引擎：断档/重叠/逆序 + 相邻交接对 + 时间线几何数据
│   ├── handover.py        # 双向交接确认：交接项生成、摘要绑定、失效检测
│   ├── db.py              # SQLite 连接、建表、演示数据
│   ├── schemas.py         # Pydantic 请求模型与输入校验
│   ├── report.py          # Markdown / HTML / JSON 报告生成
│   └── static/
│       ├── index.html     # 单页界面
│       ├── styles.css
│       └── app.js         # 原生 JS：交互、渲染、导出
├── tests/                 # 审计引擎与 API 测试
├── Dockerfile
├── docker-compose.yml
├── entrypoint.sh          # 启动时初始化数据库并启动服务
├── requirements.txt
├── requirements-dev.txt
└── README.md
```

## 技术说明

- 时间存储为 SQLite 文本 `YYYY-MM-DD HH:MM:SS`，应用层统一解析为 naive datetime 比较；
- `events.seq` 为不可变的录入顺序（自增），审计排序按 `start_time`，二者不一致即为逆序录入；
- 签署快照以 JSON 文本整体存入 `signoffs.snapshot`，并记录 SHA-256 校验值；
  撤销仅更新 `status / revoke_reason / revoked_at`，快照内容从创建起无任何接口可修改；
- 交接确认记录存入 `handover_confirmations`，创建时固化两条事件的关键字段摘要
  （`event_digest`，SHA-256）与快照（`event_summary`）；事件增删改后由同步逻辑把
  不再匹配的记录标记为 `invalid`（保留原因与时间，永不复活），同一交接项通过部分
  唯一索引保证至多一条有效确认；
- 无外部前端构建步骤，静态资源由 FastAPI 的 `StaticFiles` 直接提供；
- 服务进程在容器内以非 root 友好的方式运行于固定端口 **8080**，数据与镜像分离。
