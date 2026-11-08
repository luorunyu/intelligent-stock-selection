"""Attribute saved trade changes without rerunning either strategy."""

from pathlib import Path
import hashlib
import json
import math

import pandas as pd

OUT = Path(__file__).resolve().parent
RUNS = OUT.parents[1] / "2026-11-08"
BATCHES = {"old": "charts_20261108_101635_308715", "new": "charts_20261108_185117_945974"}


def table(text, title):
    section = text.split("## " + title + "\n", 1)[1].split("\n## ", 1)[0]
    lines = [line for line in section.splitlines() if line.startswith("| ")]
    cells = lambda line: [cell.strip() for cell in line.strip().strip("|").split("|")]
    return pd.DataFrame([cells(line) for line in lines[2:]], columns=cells(lines[0]))


def number(value):
    return None if value in (None, "-", "") else float(value.replace("%", ""))


def load(batch):
    text = (RUNS / batch / "short_trade_backtest.md").read_text()
    trades, audits = table(text, "逐笔交易"), table(text, "开平仓核对")
    rows = []
    for t, a in zip(trades.to_dict("records"), audits.to_dict("records")):
        assert (t["股票"], t["开仓日"]) == (a["股票"], a["开仓日"])
        anchor = a.get("重开关联保护平仓日 / 平仓价 / 原开仓日 / 间隔交易日", "").split(" / ")
        watch = a.get("保护平仓后观察状态 / 后续开仓日 / 拦截普通信号数", "").split(" / ")
        rows.append({
            "code": t["股票"].split()[0], "name": t["股票"].split(maxsplit=1)[-1],
            "cycle": int(t["周期"]), "state": t["开仓状态"], "source": t["信号来源"],
            "entry": t["开仓日"], "entry_price": number(t["开仓价"]),
            "end": t["结束/估值日"], "end_price": number(t["结束/估值价"]),
            "reason": a["结束原因"], "completed": t["状态"] == "已完成",
            "ret": number(t["已实现收益"]), "mark": number(t["浮动收益"]),
            "mfe": number(t["MFE"]), "mae": number(t["MAE"]), "days": int(t["持有日"]),
            "anchor_exit_date": anchor[0] if len(anchor) > 1 else None,
            "anchor_exit_price": number(anchor[1]) if len(anchor) > 1 else None,
            "parent_entry": anchor[2] if len(anchor) > 2 else None,
            "gap_days": number(anchor[3]) if len(anchor) > 3 else None,
            "watch": watch[0] if len(watch) > 1 else None,
            "reentered_date": watch[1] if len(watch) > 1 else None,
            "watch_blocked_signals": number(watch[2]) if len(watch) > 2 else 0,
        })
    return pd.DataFrame(rows)


def stats(frame):
    r = frame.loc[frame.completed, "ret"].dropna()
    pos, neg = r[r > 0], r[r < 0]
    return dict(n=len(frame), completed=len(r), wins=len(pos), losses=len(neg),
                win_rate=100 * len(pos) / len(r) if len(r) else None,
                mean=r.mean(), median=r.median(), positive_mean=pos.mean(), positive_median=pos.median(),
                negative_mean=neg.mean(), negative_median=neg.median(),
                return_sum_pp=r.sum(), positive_sum_pp=pos.sum(), negative_sum_pp=neg.sum())


def groups(frame, column):
    return [{column: key, **stats(part)} for key, part in frame.groupby(column, dropna=False)]


def clean(value):
    if isinstance(value, dict):
        return {key: clean(item) for key, item in value.items()}
    if isinstance(value, list):
        return [clean(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


old, new = (load(BATCHES[key]) for key in ("old", "new"))
hashes = {key: hashlib.sha256((RUNS / batch / "states.csv").read_bytes()).hexdigest() for key, batch in BATCHES.items()}
assert hashes["old"] == hashes["new"], "Input states differ"
metrics = dict(batches=BATCHES, state_hashes=hashes,
               old=stats(old), new=stats(new),
               new_by_source=groups(new, "source"), new_by_reason=groups(new, "reason"))
old_keys, new_keys = set(zip(old.code, old.entry)), set(zip(new.code, new.entry))
added = new[[key not in old_keys for key in zip(new.code, new.entry)]].copy()
removed = old[[key not in new_keys for key in zip(old.code, old.entry)]].copy()
common = old.merge(new, on=["code", "entry"], suffixes=("_old", "_new"))
common["delta"] = common.ret_new - common.ret_old
metrics["entry_overlap"] = dict(common=len(common), added=stats(added), removed=stats(removed))
protect = new[new.reason.eq("PROFIT_PROTECTION_EXIT")].copy()
reentry = new[new.source.eq("保护平仓价再开仓")].copy()
metrics["protection"] = dict(**stats(protect),
    mfe_le_2=int(protect.mfe.le(2).sum()), mfe_le_5=int(protect.mfe.le(5).sum()),
    mfe_mean=protect.mfe.mean(), mfe_median=protect.mfe.median(),
    reentered=int(protect.watch.eq("已重开").sum()), waiting=int(protect.watch.eq("观察中").sum()))
metrics["reentry"] = dict(**stats(reentry), by_reason=groups(reentry, "reason"),
    next_day=int(reentry.gap_days.eq(1).sum()), gap_median=reentry.gap_days.median(),
    lost_count=int((reentry.ret < 0).sum()), lost_sum_pp=reentry.loc[reentry.ret < 0, "ret"].sum())
gap = 100 * (1 - reentry.entry_price / reentry.anchor_exit_price)
metrics["reentry"]["entry_below_anchor_pct"] = dict(mean=gap.mean(), median=gap.median(), maximum=gap.max())
nonprotection_winners = new[new.completed & new.ret.gt(0) & new.reason.ne("PROFIT_PROTECTION_EXIT")]
metrics["positive_return_mix"] = dict(
    protection_winners=int(protect.ret.gt(0).sum()),
    protection_share_of_winners_pct=100 * protect.ret.gt(0).sum() / new.ret.gt(0).sum(),
    other_winners=len(nonprotection_winners), other_winner_mean=nonprotection_winners.ret.mean(),
)
paired_protect = common[common.reason_new.eq("PROFIT_PROTECTION_EXIT") & common.completed_old].copy()
metrics["same_entry_protection"] = []
for label, mask in [("old_win", paired_protect.ret_old.gt(0)), ("old_loss", paired_protect.ret_old.lt(0)), ("old_flat", paired_protect.ret_old.eq(0))]:
    part = paired_protect[mask]
    metrics["same_entry_protection"].append(dict(group=label, n=len(part), old_mean=part.ret_old.mean(),
        new_mean=part.ret_new.mean(), delta_sum_pp=part.delta.sum(), old_sum_pp=part.ret_old.sum(), new_sum_pp=part.ret_new.sum()))
metrics["unprotected_old_losses"] = dict(
    old_losses_with_zero_mfe=int((old.completed & old.ret.lt(0) & old.mfe.eq(0)).sum()),
    common_loss_unchanged=int((common.completed_old & common.ret_old.lt(0) & common.delta.abs().lt(.011)).sum()),
    common_loss_changed_to_positive=int((common.completed_old & common.ret_old.lt(0) & common.ret_new.gt(0)).sum()),
)

# A chain follows actual recorded reentry links. Its leg-return sum is only
# a signal statistic; different leg entry prices mean this is not portfolio P&L.
roots = {}
new["root_entry"] = ""
for idx, row in new.sort_values("entry").iterrows():
    parent = (row.code, row.parent_entry)
    root = roots[parent] if row.source == "保护平仓价再开仓" else row.entry
    roots[(row.code, row.entry)] = root
    new.loc[idx, "root_entry"] = root
chains = new.groupby(["code", "root_entry"]).agg(
    name=("name", "first"), legs=("entry", "size"), end=("end", "max"),
    chain_sum=("mark", "sum"), completed=("completed", "all"),
).reset_index()
chain_compare = old.merge(chains, left_on=["code", "entry"], right_on=["code", "root_entry"], suffixes=("_old", "_chain"))
chain_compare["chain_delta"] = chain_compare.chain_sum - chain_compare.mark
metrics["chains"] = dict(n=len(chains), extra_legs=int((chains.legs - 1).sum()),
    mean_sum=chains.chain_sum.mean(), same_root=len(chain_compare),
    same_root_delta_sum_pp=chain_compare.chain_delta.sum(),
    warning="Chain endpoints can differ from old trade endpoints; matched-horizon details are separate.")
same_end = chain_compare[
    chain_compare.end_old.eq(chain_compare.end_chain)
    & chain_compare.completed_old & chain_compare.completed_chain
]
metrics["same_end_chain_comparison"] = []
for label, mask in [("old_win", same_end.ret.gt(0)), ("old_loss", same_end.ret.lt(0))]:
    part = same_end[mask]
    metrics["same_end_chain_comparison"].append(dict(group=label, n=len(part),
        old_mean=part.ret.mean(), new_leg_sum_mean=part.chain_sum.mean(),
        improved=int(part.chain_delta.gt(.011).sum()), worse=int(part.chain_delta.lt(-.011).sum())))
stocks = old.groupby(["code", "name"]).mark.sum().rename("old_sum").to_frame().join(
    new.groupby(["code", "name"]).mark.sum().rename("new_sum"), how="outer").fillna(0).reset_index()
stocks["delta"] = stocks.new_sum - stocks.old_sum
metrics["stock_sum_comparison"] = dict(n=len(stocks), improved=int(stocks.delta.gt(.011).sum()),
    worse=int(stocks.delta.lt(-.011).sum()), same=int(stocks.delta.abs().le(.011).sum()),
    old_sum_pp=stocks.old_sum.sum(), new_sum_pp=stocks.new_sum.sum())

for name, frame in dict(old_trades=old, new_trades=new, common_entries=common,
                        added_entries=added, removed_entries=removed,
                        protection_same_entry=paired_protect, chains=chains,
                        chain_comparison=chain_compare, stocks=stocks).items():
    frame.to_csv(OUT / (name + ".csv"), index=False, encoding="utf-8-sig")
(OUT / "metrics.json").write_text(json.dumps(clean(metrics), ensure_ascii=False, indent=2, allow_nan=False))
print(json.dumps(clean(metrics), ensure_ascii=False, indent=2, allow_nan=False))
print("\nWORST STOCKS\n", stocks.sort_values("delta").head(10).to_string(index=False))
print("\nPROTECTION SAME ENTRY OLD WINNERS\n", paired_protect[paired_protect.ret_old.gt(0)].sort_values("delta")[["code", "name_old", "entry", "end_old", "end_new", "ret_old", "ret_new", "mfe_new"]].head(12).to_string(index=False))

summary_rows = [
    ("实际开仓", "n", False), ("已完成", "completed", False),
    ("盈利笔数", "wins", False), ("亏损笔数", "losses", False),
    ("胜率", "win_rate", True), ("全部已完成平均收益", "mean", True),
    ("盈利交易平均收益", "positive_mean", True), ("盈利交易中位收益", "positive_median", True),
    ("亏损交易平均收益", "negative_mean", True), ("亏损交易中位收益", "negative_median", True),
]
rows = []
for label, key, pct in summary_rows:
    values = [f"{metrics[k][key]:.2f}%" if pct else str(metrics[k][key]) for k in ("old", "new")]
    rows.append(f"| {label} | {values[0]} | {values[1]} |")
report = f"""# 利润保护与重开的已保存结果对比

旧批次：`{BATCHES['old']}`；新批次：`{BATCHES['new']}`。
两个 states.csv 的 SHA256 完全一致：`{hashes['old']}`。只解析已保存的逐笔交易和核对表，未重新运行策略。
收益数字使用报告中保留两位小数的百分比，因此合计及分组均值会有舍入误差。

| 指标 | 旧版本 | 新版本 |
| --- | ---: | ---: |
{chr(10).join(rows)}

## 为什么胜率提高、盈利均值下降

- 552笔保护平仓中，536笔为正收益，16笔持平；正收益部分平均1.04%，占全部986笔盈利的54.36%。
- 其余450笔盈利均值11.99%，接近旧版盈利均值11.87%。大量小盈利加入后，新版盈利均值被拉低至6.04%。
- 119笔保护平仓此前最大浮盈不超过2%，372笔不超过5%；规则不要求先有明显利润再保护，因此小幅上下震荡也会触发。
- 532笔重开中，300笔在保护平仓后下一交易日重开；重开价平均低于锚定平仓价2.18%，中位数1.63%。这段下跌在空仓时发生。

## 为什么亏损没有明显减少

- 同一开仓日匹配中，95笔旧亏损被保护平仓截断：旧平均-13.75%，新首段平均+0.99%。这是保护规则的正面效果，尚未包含后续重开。
- 同时233笔旧盈利首段被截成平均1.03%，旧首段对应整笔平均9.20%；其后的重开可能补回部分收益，不能把首段差额全部算成永久损失。
- 重开的518笔已完成交易中，142笔亏损，亏损均值-11.47%；其中69笔触发回撤止损。仅一个下跌日和回到旧平仓价，不能确认上涨已结束。
- 旧版311笔亏损中，80笔从未有过正的收盘浮盈，不具备保护资格；直接跳过0～2%收益区间的收盘反弹也不会触发保护。
- 总亏损笔数311增至322；亏损均值从-15.22%改善为-14.14%，但亏损中位数从-17.11%变为-18.18%。

## 同一笔行情的实例

### 胜宏科技：利润确实漏掉

旧交易：2025-03-31以81.01开仓，2025-04-11以71.27平仓，收益+12.02%。
新交易：2025-04-02以80.68保护平仓，收益+0.41%；2025-04-03等收盘跌到73.00才重开，2025-04-11以71.27平仓，收益+2.37%。
80.68至73.00这段下跌没有持仓。以相同初始股数衡量，新版两段合计约+2.54%，旧版约+12.02%；差额约9.48个百分点来自空仓期间的下跌。

### 中国卫星：保护后重新开仓仍可能亏损

旧交易：2026-01-15开仓，2026-01-23结束，收益-1.94%。
新交易：2026-01-19以104.89保护平仓，首段+1.82%；2026-01-20跌到98.04重开，2026-01-23反弹到108.90平仓，第二段-11.08%。
第一次保护成功并不代表重开安全；重开在更低价格做空后，反弹会放大损失。

## 口径与后续方向

新旧开仓日期共有811个；新增533个、旧版独有91个。新规则同时改变了持仓路径、重开和普通信号是否可用，不能只看首笔或只看新增交易。
新交易沿实际关联平仓记录串成812条交易链；链的结束日可能与旧交易不同。`same_end_chain_comparison`仅保留同一起点、同一结束日且都已完成的链。
CSV中的交易收益相加只用于信号比较，逐笔资金基数可能不同，不代表真实组合收益；股票汇总还包含持仓中的浮动收益。

本次规则没有使用回调/反弹的速度、持续天数或趋势确认。因此它增加了保本及小盈利的频率，但没有直接实现区分上涨回调和下跌反弹的目标。
后续应先按完整交易链检查净收益，并优先研究重开条件和收盘触发导致的漏跌，再比较保护本身对原亏损的改善。

明细见 `metrics.json`、`old_trades.csv`、`new_trades.csv`、`common_entries.csv`、`chain_comparison.csv`、`stocks.csv`。
"""
(OUT / "analysis.md").write_text(report)
