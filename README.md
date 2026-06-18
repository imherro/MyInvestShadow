# MyInvestShadow

MyInvestShadow 是一个本地影子账户 Web 系统，只消费两类已有研究结果：

- 市场结果：`https://market.okbbc.com//api/research/latest`
- 主线结果：`https://theme.okbbc.com/api/shadow-account/latest`

系统不做市场研究，不做主线研究，不连接真实下单。它根据收盘基准日生成影子目标仓位，保存净值曲线，并提供真实持仓权重对照 API。

## 启动

```powershell
python .\scripts\run_web.py
```

默认地址：

```text
http://127.0.0.1:8013
```

## 主要 API

- `GET /api/state`：页面使用的完整状态。
- `POST /api/run/daily`：立即拉取最新研究结果并生成影子仓位。
- `GET /api/nav`：资金净值曲线。
- `GET /api/allocations/latest`：最新影子目标仓位。
- `POST /api/actual-holdings`：提交真实持仓权重用于对照。
- `GET /api/compare/latest`：查看最新真实持仓对照结果。

真实持仓提交示例：

```json
{
  "as_of_date": "2026-06-17",
  "source": "manual",
  "holdings": [
    {
      "code": "588170.SH",
      "name": "示例ETF",
      "weight_ratio": 8.0
    }
  ]
}
```

## 调仓原则

- 市场接口的权益仓位区间决定总风险预算。
- 主线接口的 `theme_signals` 决定主题分配。
- 标的候选只从主线接口 `top_etf` 提取。
- 收盘价和日涨跌优先使用本地 `.env` 中的 Tushare token；不可用时回退到主线接口的 ETF 排名数据。
- 所有对外展示和对照接口都使用比例，不展示资金总额、股数或真实盈亏金额。

## 自动刷新

服务启动后会先补一次最新收盘基准日。运行期间会在每个交易日 15:05 后定时检查并刷新；同一基准日更新净值点，不重复累加。
