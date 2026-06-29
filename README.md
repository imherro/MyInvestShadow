# MyInvestShadow

MyInvestShadow 是一个本地影子账户 Web 系统。它只消费已经完成的 A 股市场研究结果和主线研究结果，不自行做市场研究，不连接真实交易，不读取真实持仓，也不输出金额、股数或真实盈亏。

系统目标是：每天收盘后，依据同一收盘基准日的市场结果和主线结果，生成一套可审计、可复盘的影子目标仓位，并维护资金净值曲线。默认落地工具是可交易 ETF；只有在主线进入资金收敛阶段、且同日 MyInvestStock 深研明确通过时，才允许用很小比例的龙头弹性仓。

## 系统边界

- 市场结果来源：`https://market.okbbc.com//api/research/latest`
- 主线结果来源：`https://theme.okbbc.com/api/latest`
- ETF 研究来源：`https://etf.okbbc.com/api/latest`
- 个股深研来源：`https://stock.okbbc.com/api/latest`
- 龙头研究来源：`https://leader.okbbc.com/api/latest`
- 本系统只做影子账户组合构建、ETF/弹性仓门禁、净值记录、页面展示和 API 输出。
- 本系统不做市场研究，不做主线研究，不生成真实交易指令。
- 本系统不读取、不比较、不优化真实持仓。
- 所有对外结果均为比例数据，不展示账户总额、持仓股数或真实盈亏金额。
- 新生成的影子目标仓位以可交易 ETF 为主；个股只作为同日深研通过后的主题弹性小仓，不作为默认持仓工具。

## 启动

```powershell
python .\scripts\run_web.py
```

默认地址：

```text
http://127.0.0.1:8013
```

如果把 `SHADOW_HOST` 改成 `0.0.0.0` 或其他非本机地址，必须同时配置 `SHADOW_API_TOKEN`。手动刷新接口需要在请求头传入 `X-Shadow-Token`，否则写入口会拒绝执行。

## 核心流程

1. 拉取市场结果和主线结果。
2. 保存两类硬依赖上游快照到 `source_snapshots`，便于排查来源状态。
3. 校验两类硬依赖均为本次成功拉取，且都带有基准日。
4. 校验市场基准日和主线基准日一致。
5. 硬依赖通过后，再拉取 ETF、个股、龙头三个可选研究上游；可选上游基准日不一致时只记录状态，不参与本次仓位。
6. 用主线 `mainline_ranking` 判断阶段，用 ETF 研究补充同方向候选，用个股深研补充极小比例龙头弹性候选。
7. 获取候选 ETF/股票的收盘价、涨跌幅、成交额、净值溢价和近期表现。
8. 根据市场结果计算主动仓位预算。
9. 对主线仓位、主题仓位和收益防御仓执行分型门禁。
10. allocator 只在已通过门禁的候选池内分配预算；未分配主动预算交给结构守恒模块处理，禁止隐性回补核心 ETF。
11. 生成目标仓位、调仓变化、净值曲线和页面/API 状态。

正式调仓默认只使用当次新拉取的市场结果和主线结果。任一接口失败、任一接口缺少基准日，或两边基准日不一致时，只记录来源状态，不生成新的影子仓位和净值点。

## 仓位结构

影子组合固定拆成四层：

- 核心仓位：市场底仓，使用宽基 ETF 篮子，不跟随单条主线频繁变化。
- 主线仓位：跟随主线结果中“主线确认”“次主线”“强修复”的方向。
- 主题仓位：偏重市场表现，从未被主线仓位占用的强主线扩散或观察突破方向中选择。
- 防御仓位：承接主动仓位预算之外的资金；内部再拆成“收益防御”和“现金防御”。当 ETF 池为空触发 safe mode 时，承接被主动收缩的风险预算。

核心仓位篮子：

- `510300.SH` 华泰柏瑞沪深300ETF，占核心仓位 60%
- `510500.SH` 南方中证500ETF，占核心仓位 30%
- `159915.SZ` 易方达创业板ETF，占核心仓位 10%

防御仓位使用两层：

- 收益防御：优先使用 MyInvestETF 同日研究中的红利低波、自由现金流等防御质量 ETF；默认观察候选包括 `512890.SH`、`159201.SZ`、`159399.SZ`。
- 现金防御：`511880.SH` 银华货币ETF-A。

`CORE.ASHARE` 和 `DEFENSIVE.CASH` 只作为旧历史记录兼容显示，不再作为新目标仓位。

## 主动仓位预算

市场研究决定影子账户的总仓位边界和仓位结构。影子账户不重新判断市场强弱，只把市场研究和主线研究翻译成可交易 ETF 组合。

优先级：

1. 如果市场结果提供 `sleeve_allocation`，优先使用市场研究给出的五仓结构区间中位数。
2. 如果没有 `sleeve_allocation`，但提供完整 `sleeve_mix`，使用旧四仓结构区间中位数。
3. 如果只有 `equity_position_range`，旧分数仓位只作为区间内取点依据，最终仓位必须落在该区间内。
4. 如果市场结果缺少官方仓位区间，才使用 `portfolio/position_sizer.py` 中的旧分数分档 fallback。

`sleeve_allocation` 映射：

- `core_wide_etf` -> 核心仓位
- `mainline_etf` -> 主线仓位
- `leader_alpha` -> 主题/龙头弹性仓位
- `defensive_quality` -> 收益防御仓位
- `cash_like` -> 现金防御仓位

兼容当前市场接口新键名：

- `beta_core` -> 核心仓位
- `alpha_active` -> 主线仓位
- `defensive_factor` -> 收益防御仓位
- `liquidity` -> 现金防御仓位

旧 `sleeve_mix` 映射：

- `sleeve_mix.core` -> 核心仓位
- `sleeve_mix.offensive` 或 `sleeve_mix.mainline` -> 主线仓位
- `sleeve_mix.thematic` -> 主题仓位
- `sleeve_mix.defensive` -> 防御仓位

如果 `sleeve_mix` 不完整，例如只提供 `thematic` 上限，则不接管全部结构；系统只把该字段作为主题上限，其他结构进入 fallback。防御仓位等于 `100% - 实际主动仓位`；门禁过滤造成的主线/主题缺口由结构守恒模块处理，不允许自动回补核心 ETF。

当市场研究处于防守期，或官方仓位区间上限不超过 20%，未落地的主线/主题预算直接回到防御仓，不再强行补给主线。这是为了遵守“指数强但宽度弱”场景下的市场风险约束。

`/api/latest` 和 `/api/index` 会输出 `allocation_policy`，用于审计：

- `position_source`：总仓位来自 `market.sleeve_allocation`、`market.sleeve_mix`、`market.equity_position_range` 还是 fallback
- `sleeve_source`：仓位层结构来自 `market.sleeve_allocation`、`market.sleeve_mix` 还是 fallback
- `fallback_used`：是否因为上游缺少官方仓位字段而使用旧分档
- `equity_position_range`、`target_active_weight_ratio`、`range_violation`
- `raw_sleeve_allocation`、`raw_sleeve_mix` 和最终 `sleeve_targets`

## 主线仓位规则

主线仓位优先读取主线结果里的 `mainline_ranking`。系统把上游 `cycle_stage`、`lifecycle_state` 映射成影子账户阶段：

- `beta_dominant`：趋势初期或政策孵化，ETF 优先。
- `beta_to_alpha`：方向已确认，先 ETF 承接，龙头只在上游确认时补充。
- `alpha_convergence`：资金向龙头收敛，可考虑龙头弹性仓。
- `emotion_game`：拥挤或情绪博弈，只小仓或空仓。
- `avoid_or_defensive`：旧线残余或退潮，回防御。

兼容旧格式时，主线仓位只看 `stage` 里的强方向：

- `stage` 包含“主线确认”
- `stage` 包含“次主线”
- `stage` 包含“强修复”

主线方向最多取排名靠前的 3 个。每个方向从主线 `top_etf` 和 MyInvestETF 同方向研究中抽取 ETF 候选，同一方向只保留成交额最大的一个代表 ETF。预算按方向信号强度分配：

- 基础权重来自 `score_weight_ratio`，缺失时使用 `evidence_score`
- “主线确认”乘数 1.00
- “次主线”或“强修复”乘数 0.85
- ETF 门禁在分配前执行，D 档方向不会进入分配池
- A/B/C 档视为可交易候选，并按门禁等级系数参与方向权重分配

## 主题仓位规则

主题仓位不是主线仓位的重复仓。它从主线结果中寻找未被主线仓位占用的方向，且更偏重市场表现。

候选范围：

- 强主线扩散：`stage` 包含“主线确认”“次主线”“强修复”，且 `evidence_score >= 75`
- 观察突破：`stage` 包含“观察”，且 `evidence_score >= 70`

排除规则：

- 已被核心仓位或主线仓位使用的 ETF 不再进入主题仓位
- 已被主线仓位占用的同一方向不再进入主题仓位
- 同一方向只保留成交额最大的代表 ETF

主题排序更重视市场表现：

- 市场表现分 55%
- ETF 门禁分 30%
- 主线匹配度 15%
- 主题排序分再乘以 ETF 门禁等级系数

如果主题预算小于 5%，最多选择 1 个主题 ETF；如果主题预算大于等于 5%，最多选择 2 个主题 ETF。市场表现不足或门禁不通过的主题预算会交给结构守恒模块：有有效非核心 ETF 时按比例重分配；没有有效 ETF 时进入 safe mode，主动仓位收缩并提高防御仓位。

## 龙头弹性仓规则

龙头弹性仓是主题仓位里的一个可选补充，不是默认股票池。只有同时满足以下条件才会出现：

- 主线阶段显示资金向龙头收敛，`instrument_preference` 为 `leader` 或 `etf_then_leader`。
- MyInvestStock 同日接口基准日与市场/主线基准日一致。
- 个股深研为 A 档，且上游标记 `shadow_observation_eligible = true`。
- 主线绑定、证据质量、交易结构、换手率等门禁通过。
- 同一方向没有已经被主线 ETF 占用。

个股弹性仓最多选 2 个，单票上限 6%，总预算不超过主题仓位预算和 8% 中的较小值。若 MyInvestStock 缺失、失败或基准日不一致，系统自动退回 ETF-only，不用旧个股结论补仓。

## ETF 评分/估值门禁

主线仓位和主题仓位落地前都必须经过 ETF 门禁。门禁不是“是否属于主线”的重复判断，而是判断某个 ETF 是否适合在当天作为影子仓位承接工具。门禁是前置过滤约束：先过滤 ETF 池，再做仓位分配。

门禁评分按仓位层分型，不再用一套权重处理所有 ETF。

主线 ETF：

- 主线匹配度 35%
- 趋势结构 25%
- 成交活跃度 20%
- 估值/拥挤度 15%
- 数据质量 5%

主题 ETF：

- 主线匹配度 30%
- 趋势结构 25%
- 成交活跃度 20%
- 估值/拥挤度 15%
- 数据质量 10%

主题仓位在评分后额外扣 5 分，因为它本身承担更高的弹性和波动。

收益防御 ETF：

- 防御匹配度 25%
- 因子溢价或估值安全 30%
- 成交活跃度 20%
- 跟踪质量 15%
- 组合角色 10%

如果 MyInvestETF 给出同日深研，系统会使用其 `deep_rating`、`deep_score`、`sleeve_key`、估值模型、流动性、跟踪和组合角色评分；如果 ETF 深研缺失，系统退回收盘价、成交额、净值溢价和近期表现的本地门禁。

门禁等级：

- A：分数 `>= 80`，进入可交易池
- B：分数 `>= 70`，进入可交易池
- C：分数 `>= 55`，进入可交易池
- D：不进入可交易池

门禁等级参与仓位权重：

- A：权重系数 `1.00`
- B：权重系数 `0.85`
- C：权重系数 `0.60`
- D：权重系数 `0.00`

主线仓位的最终方向权重为 `方向信号强度 × 阶段乘数 × 门禁等级系数`。主题仓位先按市场表现、ETF 门禁分和主线匹配度得到排序分，再乘以门禁等级系数。

硬性拒绝条件包括：

- 缺少可验证交易数据
- 成交活跃度过低
- 溢价/折价异常
- 主线匹配度不足
- 缺少收盘表现与 ETF 排名

缺失净值溢价、5 日涨幅、20 日涨幅等数据时，系统会记录到 `data_gaps` 并降低评分，不把缺失数据当作已经验证通过。

## 结构守恒约束

`portfolio/structure_guard.py` 负责在门禁过滤之后检查仓位结构，避免未分配预算被某个 sleeve 隐性吸收。

约束规则：

- 总组合恒为 `核心 + 主线 + 主题 + 防御 = 100%`
- 非 safe mode 下，主动仓位应等于 position sizer 给出的目标主动仓位
- core 只使用自己的基础预算，不吸收主线或主题失败预算
- 主线/主题未分配预算优先在已有有效非核心 ETF 池内按比例重分配
- 防守期或官方仓位上限很低时，主线/主题未分配预算直接进入防御仓
- 如果有效非核心 ETF 池为空，进入 safe mode：不回补 core，主动仓位收缩，防御仓位提高

结构守恒报告会输出：

```python
{
    "total_sum_check": True,
    "active_sum_check": True,
    "violation": False,
    "safe_mode_triggered": False,
    "unallocated_policy": "redistribute",
    "defensive_absorbed_ratio": 0.0,
}
```

## ETF 候选池展示规则

ETF 门禁表是“全方向代表备选池”，不是只展示最终持仓。

展示规则：

- 主线结果中只要方向带有 ETF 候选，就进入方向归并。
- 每个方向只展示成交额最大的一个代表 ETF。
- 已落地的主线/主题 ETF 会显示实际候选预算和执行比例。
- 未进入本次仓位的方向也会保留为备选。
- 观察线方向显示为观察备选。
- 同方向其他 ETF 不进入门禁列表，也不会进入目标仓位。

这样可以避免同一方向里多个芯片、半导体、通信 ETF 同时占用仓位。

## 价格和净值

价格数据优先级：

1. Tushare：ETF 日线、成交额、基金净值、净值溢价。
2. MyInvestETF：同日 ETF 研究中的收盘、涨跌、成交额、近 5/20 日表现和深研评分。
3. 主线接口回退数据：`latest_result.etf_top` 中的 ETF 排名、涨跌幅、成交额和近期表现。

如果本地 `.env` 中没有 Tushare token，系统仍可使用 MyInvestETF 和主线接口中的回退数据进行部分评分，但会在数据缺口中反映缺失项。

净值计算：

- 初始净值为 `1.0`
- 每个新基准日的收益来自上一期影子目标仓位乘以当日 ETF 涨跌幅
- 防御货币 ETF 如果没有涨跌幅，不主动贡献收益
- 同一基准日重复运行时，`nav_points` 使用最新 run 覆盖该基准日记录
- 调仓历史按不同基准日去重展示

## 自动刷新

服务启动后会先补一次最新收盘基准日。运行期间默认每 5 分钟检查一次调度窗口，只在晚间 `21:10`、`21:40`、`22:10` 三个窗口尝试自动生成正式影子仓位；当天任一窗口成功后不再自动重复运行，失败则等待下一个窗口重试。

并发保护：

- 手动刷新和定时刷新共享同一把进程级锁。
- 如果已有刷新正在运行，手动接口返回 `409`。
- 如果定时任务发现刷新正在运行，会跳过当前检查周期。

可用环境变量：

- `SHADOW_HOST`：监听地址，默认 `127.0.0.1`
- `SHADOW_PORT`：监听端口，默认 `8013`
- `SHADOW_REFRESH_MINUTES`：调度检查间隔，默认 `5`
- `SHADOW_SCHEDULE_TIMES`：自动调仓窗口，默认 `21:10,21:40,22:10`
- `SHADOW_API_TOKEN`：非本机监听时的写入口令牌
- `MARKET_API_URL`：市场结果接口覆盖地址
- `THEME_API_URL`：主线结果接口覆盖地址
- `ETF_API_URL`：MyInvestETF 研究接口覆盖地址
- `STOCK_API_URL`：MyInvestStock 深研接口覆盖地址
- `LEADER_API_URL`：MyInvestLeader 龙头研究接口覆盖地址
- `TUSHARE_TOKEN` / `TUSHARE_PRO_TOKEN` / `tushare_token`：Tushare token

## API

对外读取 API：

- `GET /api`：统一接口目录，返回系统名称、版本、base_url、文档入口、推荐入口、安全边界、接口分组和公开接口总数。该接口只做说明，不触发重计算、写入、交易或同步。
- `GET /api/index`：主页主要内容，包含概览、仓位结构、防御拆层、可选上游状态、净值曲线、`510300.SH`/`510500.SH` 收盘价归一化对比线、目标仓位、调仓历史和数据状态。
- `GET /api/latest`：最新影子组合完整状态，包含运行记录、ETF 门禁、个股弹性门禁、目标仓位、调仓历史和来源状态。
- `GET /api/nav`：资金净值曲线。
- `GET /api/allocations/latest`：最新影子目标仓位。
- `GET /api/rebalance-history`：按不同收盘基准日去重后的调仓历史。
- `GET /api/source-status`：市场、主线、ETF、个股、龙头接口最近一次快照状态。

写入运维 API：

- `POST /api/run/daily`：立即拉取最新研究结果并生成影子仓位。

`POST /api/run/daily` 在本机监听时可直接调用；在非本机监听时必须配置 `SHADOW_API_TOKEN` 并提供 `X-Shadow-Token` 请求头。

## 数据库

默认数据库位于：

```text
data/shadow_account.sqlite
```

主要表：

- `source_snapshots`：每次上游拉取快照，包括成功和失败。
- `shadow_runs`：每次正式影子调仓运行。
- `target_allocations`：每次运行对应的目标仓位。
- `nav_points`：按基准日维护的净值曲线。

数据库是本地运行状态，不建议放入审计包或公开传输。

## 页面

主页是一个只读仪表盘：

- 仓位结构用一行方块展示核心、主线、主题、收益防御、现金防御，方块宽度对应仓位比例。
- 资金净值曲线显示日期轴，并叠加 `510300.SH`、`510500.SH` 的收盘价归一化对比线。
- 影子目标仓位使用底色区分核心、主线、主题、防御。
- 涨跌颜色遵循 A 股习惯：上涨为红色，降低为绿色。
- ETF 门禁候选池全部展开显示，不使用内部滚动。
- 页面末尾展示接口说明，包括公开接口数量、推荐入口、功能分组和安全边界。
- 页面不展示真实持仓、不展示账户金额。

## 文件结构

```text
portfolio/
  position_sizer.py 官方仓位字段缺失时的 fallback 仓位函数
  structure_guard.py 结构守恒约束和 safe mode
etf/
  gate_filter.py    ETF 门禁前置过滤，只过滤可交易池，不做仓位缩放
shadow_app/
  allocator.py      仓位结构拆分、主线/主题选择、ETF 去重和目标仓位生成
  etf_gate.py       ETF 评分/估值门禁
  etf_research.py   MyInvestETF 研究解析、同方向 ETF 补充和防御质量候选
  stock_research.py MyInvestStock 深研解析和龙头弹性门禁
  phase.py          主线周期阶段到影子账户工具偏好的映射
  pricing.py        Tushare 价格、净值溢价和主线接口回退价格
  service.py        调仓运行、数据库写入、净值和 API 状态组装
  upstream.py       市场/主线/ETF/个股/龙头接口拉取和主线结果格式归一化
  main.py           FastAPI 路由、定时刷新和写入口保护
  db.py             SQLite schema 和基础读写工具
  config.py         环境变量和运行配置
static/
  app.js            页面数据加载和渲染
  app.css           页面样式
templates/
  index.html        首页模板
tests/
  test_system_stability.py regime sweep、ETF 池崩塌、噪声扰动和确定性压力测试
  test_position_sizer.py fallback 仓位函数边界、confidence、regime 和确定性测试
  test_structure_guard.py 结构守恒和 safe mode 测试
  test_gate_filter.py ETF 前置过滤测试
  test_allocator.py  仓位和门禁规则测试
  test_service.py    上游严格校验和正式运行保护测试
  test_main_api.py   写入口保护和并发拒绝测试
  test_schedule.py   定时窗口测试
```

## 测试

```powershell
python -m pytest -q
```

当前测试覆盖重点：

- 主线/主题仓位拆分
- 主线 `mainline_ranking` 周期阶段映射
- 市场 `sleeve_allocation` 五仓结构优先级
- 市场 `equity_position_range` 和完整 `sleeve_mix` 驱动仓位
- 防守期未落地预算回防御
- MyInvestETF 同方向候选补充、门禁参考和收益防御仓拆层
- MyInvestStock 同日龙头弹性仓和缺失回退
- 系统稳定性：regime sweep、ETF universe collapse、signal noise、100 次确定性重复
- fallback 仓位函数的 score 边界、confidence 调整、regime 调整和确定性
- ETF 门禁前置过滤，等级参与候选权重，但不做事后仓位折扣
- 结构守恒、非核心池重分配和 safe mode
- 同方向只保留成交额最大 ETF
- 主题仓位偏重市场表现
- ETF 门禁 A/B/C/D 过滤和拒绝
- 失败上游不生成正式调仓
- 市场/主线基准日不一致不生成正式调仓
- 写入口令牌保护
- 手动刷新并发拒绝
- 自动刷新时间窗口
- 调仓历史和主页 API 输出

## 审计关注点

建议审计时重点看：

- 是否真的没有读取真实持仓或输出真实交易指令。
- 总仓位是否优先来自市场研究的 `sleeve_mix` / `equity_position_range`，且没有越过官方区间。
- `compute_target_position` 是否只在官方仓位字段缺失时作为 fallback 使用。
- `run_daily_rebalance` 是否在上游失败或基准日不一致时停止生成正式仓位。
- 主线仓位和主题仓位是否会选择重复方向。
- MyInvestETF 是否只作为同日可选研究上游参与，过期 ETF 研究是否被忽略。
- MyInvestStock 是否只在同日、A 档、强绑定、强证据时补充小比例弹性仓。
- ETF 门禁是否在 allocator 分配前执行，并通过 `gate_universe_audit` 暴露过滤前后数量。
- ETF 门禁是否会因为数据缺失而误判为通过。
- 门禁过滤掉的主动预算是否没有隐性回补核心 ETF。
- 有效 ETF 池为空时是否触发 `safe_mode_triggered` 并提高防御仓位。
- 在 bull/neutral/crash/low-liquidity、ETF 池崩塌和信号噪声下是否仍无结构漂移。
- `/api/run/daily` 是否在外部监听时受到令牌保护。
- 页面和 API 是否保持 ratio-only。

## 审计包建议

给外部审计时建议包含：

- `README.md`
- `requirements.txt`
- `portfolio/`
- `etf/`
- `shadow_app/`
- `static/`
- `templates/`
- `tests/`
- `scripts/`
- `数据源.md`

建议排除：

- `.git/`
- `.env`
- `data/`
- `temp/`
- `.pytest_cache/`
- `__pycache__/`

这些排除项包含本地运行状态、密钥、缓存或打包产物，不影响代码逻辑审计。
