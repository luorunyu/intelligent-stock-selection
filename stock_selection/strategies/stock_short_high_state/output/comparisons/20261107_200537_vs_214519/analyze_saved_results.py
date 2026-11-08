from pathlib import Path
import hashlib
import math
import json
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
BATCHES = {
    "base": "charts_20261107_073833_667304",
    "prev": "charts_20261107_200537_774605",
    "new": "charts_20261107_214519_423415",
}

def table(text, name):
    section = text.split("## " + name + "\n", 1)[1].split("\n## ", 1)[0]
    lines = [s for s in section.splitlines() if s.startswith("| ")]
    split = lambda s: [x.strip() for x in s.strip().strip("|").split("|")]
    return pd.DataFrame([split(s) for s in lines[2:]], columns=split(lines[0]))

def number(s):
    return None if s in ("-", "", None) else float(s.replace("%", ""))

def load(batch):
    p = ROOT / "2026-11-07" / batch
    text = (p / "short_trade_backtest.md").read_text()
    trades = table(text, "逐笔交易")
    audit = table(text, "开平仓核对")
    rows = []
    for t, a in zip(trades.to_dict("records"), audit.to_dict("records")):
        assert (t["股票"], t["开仓日"]) == (a["股票"], a["开仓日"])
        chain = a.get("交易链 / 序号 / 突破资格日期及涨幅", "").split(" / ")
        watch = a.get("本次平仓后观察状态 / 截止日 / 剩余交易日 / 后续开仓", "").split(" / ")
        anchor = a.get("再开仓关联平仓日 / 价 / 原因 / 间隔交易日", "").split(" / ")
        obs = a["当日实际状态 / 收盘 / 波谷确认日"].split(" / ")
        rows.append(dict(code=t["股票"].split()[0], name=t["股票"].split(maxsplit=1)[-1], cycle=int(t["周期"]),
            entry=t["开仓日"], entry_price=number(t["开仓价"]), end=t["结束/估值日"], exit_price=number(t["结束/估值价"]),
            source=t["信号来源"], reason=a["结束原因"], completed=t["状态"]=="已完成",
            ret=number(t["已实现收益"]), mark=number(t["浮动收益"]), mfe=number(t["MFE"]), mae=number(t["MAE"]), days=int(t["持有日"]),
            chain=chain[0] if chain else "", sequence=int(chain[1]) if len(chain)>1 else 1,
            qualification_date=chain[2] if len(chain)>2 else t["开仓日"],
            watch=watch[0] if watch else "", deadline=watch[1] if len(watch)>1 else "", consumed=watch[3] if len(watch)>3 else "",
            anchor_date=anchor[0] if len(anchor)>1 else "", anchor_price=number(anchor[1]) if len(anchor)>1 else None,
            anchor_reason=anchor[2] if len(anchor)>2 else "", exit_observed=obs[0], audit=json.dumps(a,ensure_ascii=False)))
    frame=pd.DataFrame(rows)
    frame.to_csv(OUT/(batch+"_trades.csv"),index=False,encoding="utf-8-sig")
    return frame

def stats(frame):
    closed=frame[frame.completed]
    r=closed.ret.dropna()
    pos=r[r>0]; neg=r[r<0]
    return dict(n=len(frame),closed=len(closed),wins=len(pos),losses=len(neg),win_rate=100*len(pos)/len(r) if len(r) else None,
        mean=r.mean(),median=r.median(),pos_mean=pos.mean(),pos_median=pos.median(),neg_mean=neg.mean(),
        sum=r.sum(),pos_sum=pos.sum(),neg_sum=neg.sum())

def group_stats(frame, columns):
    return [{**dict(zip(columns,k if isinstance(k,tuple) else (k,))),**stats(v)} for k,v in frame.groupby(columns,dropna=False)]

frames={k:load(v) for k,v in BATCHES.items()}
summary={"batches":BATCHES,"hashes":{k:hashlib.sha256((ROOT/"2026-11-07"/v/"states.csv").read_bytes()).hexdigest() for k,v in BATCHES.items()},"trade_metrics":{k:stats(v) for k,v in frames.items()},"by_source":{k:group_stats(v,["source"]) for k,v in frames.items()},"by_reason":{k:group_stats(v,["reason"]) for k,v in frames.items()}}
stocks=None
for k,f in frames.items():
    g=f.groupby(["code","name"]).agg(**{k+"_sum":("mark","sum"),k+"_n":("mark","size")}).reset_index()
    stocks=g if stocks is None else stocks.merge(g,on=["code","name"],how="outer")
stocks=stocks.fillna(0)
summary["stock_metrics"]={}
for k in frames:
    r=stocks[k+"_sum"]; pos=r[r>0]; neg=r[r<0]
    summary["stock_metrics"][k]=dict(n=len(r),wins=len(pos),losses=len(neg),mean=r.mean(),median=r.median(),pos_mean=pos.mean(),pos_median=pos.median(),neg_mean=neg.mean(),sum=r.sum())
for old in ("prev","base"):
    a=frames[old]; b=frames["new"]
    common=a.merge(b,on=["code","entry"],suffixes=("_old","_new"))
    closed=common[common.completed_old & common.completed_new].copy()
    closed["delta"]=closed.ret_new-closed.ret_old
    def fixed(v):
        return dict(n=len(v),old_mean=v.ret_old.mean(),new_mean=v.ret_new.mean(),old_median=v.ret_old.median(),new_median=v.ret_new.median(),delta_sum=v.delta.sum(),improved=int((v.delta>.001).sum()),worse=int((v.delta<-.001).sum()),same=int((v.delta.abs()<=.001).sum()))
    keyset_a=set(zip(a.code,a.entry)); keyset_b=set(zip(b.code,b.entry))
    added=b[[x not in keyset_a for x in zip(b.code,b.entry)]].copy()
    removed=a[[x not in keyset_b for x in zip(a.code,a.entry)]].copy()
    summary[old+"_comparison"]={"common":len(common),"both_closed":fixed(closed),"old_winners":fixed(closed[closed.ret_old>0]),"old_losers":fixed(closed[closed.ret_old<0]),"winner_to_loser":int(((closed.ret_old>0)&(closed.ret_new<0)).sum()),"loser_to_winner":int(((closed.ret_old<0)&(closed.ret_new>0)).sum()),"added":stats(added),"removed":stats(removed),"added_by_source":group_stats(added,["source"]),"removed_by_source":group_stats(removed,["source"])}
    common.to_csv(OUT/(old+"_paired.csv"),index=False,encoding="utf-8-sig")
    added.to_csv(OUT/(old+"_added.csv"),index=False,encoding="utf-8-sig")
    removed.to_csv(OUT/(old+"_removed.csv"),index=False,encoding="utf-8-sig")
    closed.sort_values("delta").to_csv(OUT/(old+"_paired_closed.csv"),index=False,encoding="utf-8-sig")
    stocks[old+"_delta"]=stocks.new_sum-stocks[old+"_sum"]
    summary[old+"_stock_groups"]=[]
    for label,mask in [("old_positive",stocks[old+"_sum"]>0),("old_negative",stocks[old+"_sum"]<0)]:
        v=stocks[mask]
        summary[old+"_stock_groups"].append(dict(group=label,n=len(v),old_mean=v[old+"_sum"].mean(),new_mean=v.new_sum.mean(),old_median=v[old+"_sum"].median(),new_median=v.new_sum.median(),improved=int((v[old+"_delta"]>.001).sum()),worse=int((v[old+"_delta"]<-.001).sum()),same=int((v[old+"_delta"].abs()<=.001).sum()),new_positive=int((v.new_sum>0).sum()),new_negative=int((v.new_sum<0).sum())))
stocks.sort_values("prev_delta").to_csv(OUT/"stocks.csv",index=False,encoding="utf-8-sig")
summary["reentry_by_anchor_reason"]=group_stats(frames["new"][frames["new"].source.eq("平仓价再开仓")],["anchor_reason"])
summary["new_watch_counts"]=frames["new"].watch.value_counts().to_dict()
def clean(value):
    if isinstance(value, dict): return {k:clean(v) for k,v in value.items()}
    if isinstance(value, list): return [clean(v) for v in value]
    if isinstance(value, float) and not math.isfinite(value): return None
    return value
summary=clean(summary)
(OUT/"metrics.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2,allow_nan=False))
print(json.dumps(summary,ensure_ascii=False,indent=2))
