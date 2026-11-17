# 行证

行证为教师发展办公室提供本地实践证据台账、统计与发布工具，使用 Python 3.11+ 标准库及 SQLite，无需网络或第三方服务。

## 运行

```sh
python3 -m practice_ledger --db ./practice.sqlite demo --fixture fixtures/practice.json
python3 -m practice_ledger --db ./practice.sqlite serve --host 127.0.0.1 --port 8765
python3 -m unittest discover -s tests -v
python3 -m compileall -q practice_ledger tests
```

CLI 默认导入示例批次、审核示例记录、预览并发布报告。服务启动后接受 JSON POST：`/teachers`、`/batches/preview`、`/batches`、`/decisions`、`/reports/preview`、`/reports/monthly`、`/reports/publish`、`/batches/readiness`；GET：`/teachers`、`/batches`、`/activities`、`/activities/{id}`、`/quality`、`/audit`、`/integrity`、`/reports/latest`、`/reports/{id}`、`/reports/{id}/csv`。接口及字段见 `fixtures/practice.json` 和 `practice_ledger/transport.py`。业务错误以JSON返回；请求均只绑定回环地址。

时间戳必须含 UTC offset。报告日期使用 UTC 自然日、半开区间 `[start,end)`，每人每日最多计入480分钟；不同企业来源时间重合时按时间并集计入，统计周期和阈值在预览请求指定。隐私摘要按最小人数阈值抑制，并执行互补抑制。报告快照不可修改；输入变化后最新指针标记过期，须重新预览和发布。
