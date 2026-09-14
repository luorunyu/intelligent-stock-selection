# 个股与申万行业缓存策略

## 原则

- 按交易日批量拉取全市场 `daily`，不要逐只股票调用 `pro_bar` 构建全市场历史。
- 同一交易日批量拉取 `sw_daily`，与 `daily` 使用相同缓存日期。
- 静态表低频刷新，刷新后重建 `sw_industry_membership`。
- 分析代码只读缓存；缺数由采集脚本显式补齐。
- 重复执行默认跳过已有缓存，只有 `--force` 覆盖。

## 缓存结构

```text
data_cache/tushare/
├─ daily/YYYYMMDD.parquet
├─ sw_daily/YYYYMMDD.parquet
├─ trade_cal/YYYYMMDD.parquet
├─ static/stock_basic.parquet
├─ static/index_classify.parquet
├─ static/index_member_all.parquet
├─ static/sw_industry_membership.parquet
└─ _metadata/
   ├─ available_apis.json
   ├─ sw_static.json
   ├─ market_daily/YYYYMMDD.json
   ├─ calls.jsonl
   ├─ runs.jsonl
   └─ errors.jsonl
```

## 首次初始化

1. 配置 `TUSHARE_TOKEN`。
2. 探测当前7个接口。
3. 采集静态数据。
4. 验证 SW2021 目录包含 L1/L2/L3。
5. 由 `index_member_all` 生成 `sw_industry_membership`。
6. 按需要回填 `daily` 和 `sw_daily`。

```powershell
python scripts/probe_tushare_apis.py --date latest
python scripts/collect_tushare_daily.py static
python scripts/collect_tushare_daily.py backfill --start 20260101 --end 20260914
```

## 每日增量

```powershell
python scripts/collect_tushare_daily.py daily --date latest
```

执行顺序：

1. 通过 `trade_cal` 确认最新开市日。
2. 采集该日 `trade_cal`、`daily`、`sw_daily`。
3. 校验必需字段和非空结果。
4. 写入 `_metadata/market_daily/YYYYMMDD.json`。
5. 只有 `complete=true` 的日期才能用于个股与行业联合研究。

## 历史回填

```powershell
python scripts/collect_tushare_daily.py backfill --start 20250101 --end 20260914
```

- 先一次性取得区间交易日。
- 按交易日调用完整日频采集。
- 已有缓存默认跳过。
- 中途中断后可直接重跑。
- 不要对全市场逐只调用 `pro_bar`。

## 静态刷新

建议按月刷新，或在申万成分调整、股票上市状态变化后刷新：

```powershell
python scripts/collect_tushare_daily.py static --force
```

静态刷新必须完成：

```text
stock_basic
index_classify
index_member_all
→ rebuild sw_industry_membership
```

派生映射比任一原始静态表旧时，读取阶段自动重建。

## 权限探测

权限探测与正式采集分离：

- `index_member_all` 探测使用代表性一级行业，只验证接口可调用。
- 正式静态采集使用完整参数。
- `sample_rows` 不是全量缓存行数。
- `full_collection_required=true` 表示仍需执行静态采集。

## 查询约定

- `load_stock_daily`：从全市场 `daily` 缓存拼出单股未复权日K。
- `load_sw_daily`：从 `sw_daily` 缓存拼出行业日K，可按L1/L2/L3过滤。
- `load_sw_catalog`：读取SW2021行业目录。
- `load_sw_industry_membership`：读取或重建股票三级行业映射。
- `call_pro_bar`：只在明确需要单股复权行情时按需调用。

## 故障处理

- 无权限：记录错误并停止使用该接口，不绕过。
- 返回空表：先确认是否交易日及盘后数据是否已更新。
- 字段缺失：视为采集失败，不写成完整日期。
- `daily` 与 `sw_daily` 日期不一致：使用共同完整日期。
- 静态映射缺失：先运行 `static`，不要使用基础行业字段替代。
