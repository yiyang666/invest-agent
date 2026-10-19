# ADR-0042 多来源指数日终数据入库与总览展示

日期：2026-10-16；状态：接受。

## 决策

总览指数卡片只读取本地 `market_numeric_observations` 中已通过市场批次质量门禁的数据，不在渲染时调用任何远端接口。每个指数明确显示观察日期与来源；跨时区、休市或发布延迟时保留真实日期，不补造当日值。涨跌使用来源已核验的日涨跌幅，或由相邻有效收盘点位确定性计算。

沿用金十精选的六项指数；新增上交所公开日终页的上证指数 `000001`、科创综指 `000680`，国证指数公开日线的创业板指 `399006`，以及 Nasdaq 原始指数经 FRED 发布的 `NASDAQCOM`（纳斯达克综合）与 `NASDAQ100`（纳斯达克100）。采集器固定 URL、低频、单次请求、无隐藏换源，先归档原始响应，再校验代码、名称、日期、数值、涨跌一致性与 schema，最后发布到私有市场表。统一 `market_daily_series` 每工作日晚间触发；Web 手动按钮仍按 ADR-0036 只刷新金十。

股叉叉 `get_index_valuation` 在同一响应中包含指数点位，但实测 `change_pct` 与日涨跌不符，`snapshot_date` 也不能可靠表示点位交易日。该字段只保留为估值接口原始证据，不用作行情卡片。2026-10-15 调试时一批曾将其误写入 `index_quote:guchacha:*`；页面允许列表现已排除该系列，后续采集不再发布。保留原始批次与已发布记录供审计，不在网页使用。

## 使用边界

上交所、国证指数与 FRED 数据仅用于用户本机个人研究；指数点位和原始响应位于 Git 忽略目录，不对外公开或转载。国证指数的[授权说明](https://www.cnindex.com.cn/zh_services/data_licensing/index.html?act_menu=4&act_service=3)要求对外转发指数行情另行授权，因此此能力不能直接迁移到公开站点。上交所[公开指数页](https://www.sse.com.cn/market/sseindex/quotation/index.shtml)、国证指数[创业板指页面](https://www.cnindex.com.cn/module/index-detail.html?activeName=2&indexCode=399006)和 FRED 的 [NASDAQCOM](https://fred.stlouisfed.org/series/NASDAQCOM)、[NASDAQ100](https://fred.stlouisfed.org/series/NASDAQ100) 是页面来源。FRED 旧历史缺少原始发布时间，只作研究视图；这条数据链不自动升格为策略、风控或交易依据。

## 后果

指数卡片变成按涨跌着色的紧凑网格，先于账户快照展示；颜色只辅助阅读，点位、涨跌、日期与来源仍是判断依据。一个来源失败时其他来源仍可入库，缺失数据不由网页运行时换源。
