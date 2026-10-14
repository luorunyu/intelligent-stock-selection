"""Interactive charts and an industry browser for strategy one."""

from __future__ import annotations

from collections import defaultdict
from html import escape
import json
from pathlib import Path
from typing import Any, Iterable, Mapping

import pandas as pd

from stock_selection.data.tushare_cache import DEFAULT_CACHE_ROOT
from stock_selection.strategies.industry_index_rebound.screen import (
    calculate_industry_index_screen,
)


STRATEGY_DIRECTORY = Path(__file__).resolve().parent
DEFAULT_OUTPUT_ROOT = STRATEGY_DIRECTORY / "output"
DEFAULT_DICTIONARY_PATH = Path(
    "data_cache/tushare/static/sw_industry_stock_dictionary.json"
)
LEVEL_LABELS = {"L1": "一级行业", "L2": "二级行业", "L3": "三级行业"}
RISE_COLOR = "#d84a3a"
FALL_COLOR = "#169c72"
WINDOW_COLOR = "rgba(224, 171, 41, 0.14)"


def load_industry_catalog(
    dictionary_path: str | Path = DEFAULT_DICTIONARY_PATH,
) -> tuple[list[dict[str, Any]], dict[str, list[list[dict[str, str]]]]]:
    """Read every L1/L2/L3 node and build current stock-to-path mappings."""
    payload = json.loads(Path(dictionary_path).read_text(encoding="utf-8"))
    hierarchy = payload.get("level1")
    if not isinstance(hierarchy, dict):
        raise ValueError("industry dictionary does not contain a nested level1 tree")

    nodes: list[dict[str, Any]] = []
    stock_paths: defaultdict[str, list[list[dict[str, str]]]] = defaultdict(list)
    for l1_code, l1_node in hierarchy.items():
        l1_ref = _industry_ref(l1_code, l1_node, "L1")
        nodes.append(_catalog_node(l1_ref, [l1_ref]))
        for l2_code, l2_node in (l1_node.get("children") or {}).items():
            l2_ref = _industry_ref(l2_code, l2_node, "L2")
            l2_path = [l1_ref, l2_ref]
            nodes.append(_catalog_node(l2_ref, l2_path))
            for l3_code, l3_node in (l2_node.get("children") or {}).items():
                l3_ref = _industry_ref(l3_code, l3_node, "L3")
                l3_path = [l1_ref, l2_ref, l3_ref]
                stock_codes = sorted(
                    {
                        str(code).strip()
                        for code in (l3_node.get("stock_codes") or [])
                        if str(code).strip()
                    }
                )
                nodes.append(_catalog_node(l3_ref, l3_path, stock_codes))
                for stock_code in stock_codes:
                    stock_paths[stock_code].append([dict(item) for item in l3_path])

    return nodes, dict(stock_paths)


def load_cached_industry_bars(
    *,
    as_of_date: str | None = None,
    cache_root: str | Path | None = None,
) -> pd.DataFrame:
    """Load and normalize cached SW index OHLCV bars up to one date."""
    root = Path(cache_root) if cache_root is not None else DEFAULT_CACHE_ROOT
    target = _normalise_date(as_of_date)
    if as_of_date is not None and not target:
        raise ValueError("as_of_date must be a valid date")
    paths = sorted((root / "sw_daily").glob("*.parquet"))
    if target:
        paths = [path for path in paths if path.stem <= target]
    if not paths:
        raise FileNotFoundError(f"no sw_daily cache files found under {root / 'sw_daily'}")

    frames = [pd.read_parquet(path) for path in paths]
    return normalise_industry_bars(pd.concat(frames, ignore_index=True))


def normalise_industry_bars(data: pd.DataFrame) -> pd.DataFrame:
    """Normalize cached SW index bars for Plotly and metric calculation."""
    columns = [
        "index_code",
        "name",
        "trade_date",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "pct_chg",
    ]
    if data is None or data.empty:
        return pd.DataFrame(columns=columns)

    aliases = {
        "index_code": ("index_code", "ts_code"),
        "name": ("name", "index_name"),
        "trade_date": ("trade_date", "date"),
        "open": ("open",),
        "high": ("high",),
        "low": ("low",),
        "close": ("close",),
        "volume": ("vol", "volume"),
        "pct_chg": ("pct_chg", "pct_change", "change_pct"),
    }
    result = pd.DataFrame(index=data.index)
    for target, choices in aliases.items():
        source = next((column for column in choices if column in data.columns), None)
        result[target] = data[source] if source else None

    for column in ("index_code", "name"):
        result[column] = result[column].fillna("").astype(str).str.strip()
    result["trade_date"] = result["trade_date"].map(_normalise_date)
    for column in ("open", "high", "low", "close", "volume", "pct_chg"):
        result[column] = pd.to_numeric(result[column], errors="coerce")
    result["volume"] = result["volume"].fillna(0.0)
    result = result[
        result["index_code"].ne("")
        & result["trade_date"].ne("")
        & result[["open", "high", "low", "close"]].notna().all(axis=1)
    ]
    result = result.drop_duplicates(["index_code", "trade_date"], keep="last")
    return result[columns].sort_values(["index_code", "trade_date"]).reset_index(
        drop=True
    )


def build_industry_rebound_figure(
    bars: pd.DataFrame,
    *,
    index_code: str,
    index_name: str,
    level: str,
    metric: Mapping[str, Any] | pd.Series | None = None,
    window: int = 20,
    initial_bars: int | None = 120,
) -> Any:
    """Build one K-line chart with strategy-one window and low markers."""
    if window < 1:
        raise ValueError("window must be at least 1")
    data = normalise_industry_bars(bars)
    data = data[data["index_code"].eq(index_code)].copy()
    if data.empty:
        raise ValueError(f"no valid OHLC bars found for {index_code}")

    if metric is None:
        metric_frame = data.assign(name=index_name, level=level)
        calculated = calculate_industry_index_screen(metric_frame, window=window)
        metric = calculated.iloc[0] if not calculated.empty else {}

    go, make_subplots = _import_plotly()
    data["date"] = pd.to_datetime(data["trade_date"], format="%Y%m%d")
    dates = data["date"]
    colors = [
        RISE_COLOR if close >= open_price else FALL_COLOR
        for open_price, close in zip(data["open"], data["close"])
    ]
    figure = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        row_heights=[0.78, 0.22],
        vertical_spacing=0.03,
    )
    figure.add_trace(
        go.Candlestick(
            x=dates,
            open=data["open"],
            high=data["high"],
            low=data["low"],
            close=data["close"],
            increasing_line_color=RISE_COLOR,
            decreasing_line_color=FALL_COLOR,
            increasing_fillcolor=RISE_COLOR,
            decreasing_fillcolor=FALL_COLOR,
            name="K线",
        ),
        row=1,
        col=1,
    )
    figure.add_trace(
        go.Bar(
            x=dates,
            y=data["volume"],
            marker_color=colors,
            marker_line_width=0,
            opacity=0.72,
            name="成交量",
            hovertemplate="%{x|%Y-%m-%d}<br>成交量 %{y:,.0f}<extra></extra>",
        ),
        row=2,
        col=1,
    )

    window_data = data.tail(window)
    figure.add_vrect(
        x0=window_data["date"].iloc[0] - pd.Timedelta(hours=12),
        x1=window_data["date"].iloc[-1] + pd.Timedelta(hours=12),
        fillcolor=WINDOW_COLOR,
        line_width=0,
        layer="below",
        row="all",
        col=1,
    )

    low_date = _metric_text(metric, "window_low_date")
    if low_date:
        low_row = data[data["trade_date"].eq(low_date)].tail(1)
        if not low_row.empty:
            figure.add_trace(
                go.Scatter(
                    x=low_row["date"],
                    y=low_row["close"],
                    mode="markers+text",
                    marker={"symbol": "triangle-up", "size": 13, "color": "#b58400"},
                    text=["窗口低点"],
                    textposition="bottom center",
                    hovertemplate=(
                        "%{x|%Y-%m-%d}<br>窗口最低收盘 %{y:,.2f}<extra></extra>"
                    ),
                    name="窗口低点",
                ),
                row=1,
                col=1,
            )

    latest = data.tail(1)
    figure.add_trace(
        go.Scatter(
            x=latest["date"],
            y=latest["close"],
            mode="markers",
            marker={"symbol": "circle", "size": 10, "color": "#3b5b92"},
            hovertemplate="%{x|%Y-%m-%d}<br>最新收盘 %{y:,.2f}<extra></extra>",
            name="最新点",
        ),
        row=1,
        col=1,
    )

    for row in (1, 2):
        figure.update_xaxes(
            rangeslider_visible=False,
            showgrid=True,
            gridcolor="#edf0f2",
            rangebreaks=[{"bounds": ["sat", "mon"]}],
            row=row,
            col=1,
        )
    if initial_bars and len(data) > initial_bars:
        initial_range = [
            data["date"].iloc[-initial_bars],
            data["date"].iloc[-1] + pd.Timedelta(days=1),
        ]
        for row in (1, 2):
            figure.update_xaxes(range=initial_range, row=row, col=1)

    title = f"{LEVEL_LABELS.get(level, level)} · {index_name} {index_code}"
    figure.update_layout(
        title={"text": title, "x": 0.01, "xanchor": "left"},
        height=760,
        template="plotly_white",
        paper_bgcolor="#ffffff",
        plot_bgcolor="#ffffff",
        margin={"l": 72, "r": 32, "t": 106, "b": 48},
        hovermode="x unified",
        dragmode="zoom",
        bargap=0.16,
        font={
            "family": "Arial, PingFang SC, Microsoft YaHei, sans-serif",
            "size": 12,
            "color": "#22262b",
        },
        legend={"orientation": "h", "x": 0.01, "y": 1.04},
        uirevision="industry-index-rebound",
        annotations=[
            {
                "text": _metric_summary(metric, window),
                "xref": "paper",
                "yref": "paper",
                "x": 0.01,
                "y": 1.09,
                "showarrow": False,
                "align": "left",
                "font": {"size": 12, "color": "#50565c"},
            }
        ],
    )
    figure.update_yaxes(title_text="指数点位", row=1, col=1)
    figure.update_yaxes(title_text="成交量", rangemode="tozero", row=2, col=1)
    return figure


def write_industry_rebound_html(
    bars: pd.DataFrame,
    output_path: str | Path,
    **figure_kwargs: Any,
) -> Path:
    """Write one strategy-one chart using a shared local Plotly bundle."""
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    figure = build_industry_rebound_figure(bars, **figure_kwargs)
    figure.write_html(
        path,
        include_plotlyjs="directory",
        full_html=True,
        config={
            "displaylogo": False,
            "responsive": True,
            "scrollZoom": True,
            "modeBarButtonsToRemove": ["lasso2d", "select2d"],
        },
    )
    return path


def build_stock_search_records(
    stock_basic: pd.DataFrame,
    stock_paths: Mapping[str, list[list[dict[str, str]]]],
    available_codes: Iterable[str],
) -> list[dict[str, Any]]:
    """Build compact stock/name/path records embedded in the static index."""
    available = set(available_codes)
    if stock_basic is None or stock_basic.empty or "ts_code" not in stock_basic:
        codes = sorted(stock_paths)
        basic = pd.DataFrame({"ts_code": codes, "name": [""] * len(codes)})
    else:
        basic = stock_basic.copy()
    if "name" not in basic:
        basic["name"] = ""
    if "symbol" not in basic:
        basic["symbol"] = basic["ts_code"].astype(str).str.split(".").str[0]

    basic["ts_code"] = basic["ts_code"].fillna("").astype(str).str.strip()
    basic["symbol"] = basic["symbol"].fillna("").astype(str).str.strip()
    basic["name"] = basic["name"].fillna("").astype(str).str.strip()
    basic = basic[basic["ts_code"].ne("")].drop_duplicates("ts_code")
    records: list[dict[str, Any]] = []
    for row in basic.sort_values("ts_code").itertuples(index=False):
        paths = []
        for path in stock_paths.get(row.ts_code, []):
            paths.append(
                [
                    {
                        **industry,
                        "href": (
                            _chart_filename(industry["level"], industry["code"])
                            if industry["code"] in available
                            else ""
                        ),
                    }
                    for industry in path
                ]
            )
        records.append(
            {
                "code": row.ts_code,
                "symbol": row.symbol,
                "name": row.name,
                "paths": paths,
            }
        )
    return records


def write_industry_chart_index(
    output_directory: str | Path,
    *,
    catalog: list[dict[str, Any]],
    stock_records: list[dict[str, Any]],
    available_codes: Iterable[str],
    metric_by_code: Mapping[str, Mapping[str, Any]],
    as_of_date: str,
    window: int,
) -> Path:
    """Write the stock-search and L1/L2/L3 browse page."""
    output = Path(output_directory)
    output.mkdir(parents=True, exist_ok=True)
    available = set(available_codes)
    level_counts = {
        level: sum(node["level"] == level for node in catalog)
        for level in LEVEL_LABELS
    }
    chart_counts = {
        level: sum(
            node["level"] == level and node["code"] in available
            for node in catalog
        )
        for level in LEVEL_LABELS
    }
    sections = "".join(
        _render_level_section(
            level,
            catalog,
            available,
            metric_by_code,
            hidden=level != "L1",
        )
        for level in LEVEL_LABELS
    )
    stock_json = json.dumps(stock_records, ensure_ascii=False, separators=(",", ":"))
    stock_json = stock_json.replace("</", "<\\/")
    display_date = _display_date(as_of_date)
    html = f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>策略一申万行业图表 · {display_date}</title>
<style>
:root{{--ink:#202428;--muted:#697077;--line:#dfe3e6;--soft:#f4f6f7;--link:#245ea8;--ok:#187657;--warn:#8a6810}}
*{{box-sizing:border-box}}
body{{margin:0;color:var(--ink);background:#fff;font-family:Arial,"PingFang SC","Microsoft YaHei",sans-serif}}
header{{border-bottom:1px solid var(--line);background:#f7f8f8}}
.wrap{{max-width:1220px;margin:0 auto;padding:24px}}
h1{{font-size:24px;margin:0 0 8px}}
h2{{font-size:18px;margin:28px 0 12px}}
p{{margin:7px 0;color:var(--muted);line-height:1.6}}
.summary{{display:flex;gap:18px;flex-wrap:wrap;margin-top:16px}}
.summary strong{{font-size:18px;color:var(--ink)}}
.search{{margin:24px 0 16px}}
input{{width:100%;max-width:620px;height:42px;border:1px solid #aeb5ba;border-radius:6px;padding:0 12px;font-size:15px}}
input:focus{{outline:2px solid #91add0;outline-offset:1px}}
#stockResults{{margin-top:12px;max-width:900px}}
.stock-result{{padding:12px 0;border-bottom:1px solid var(--line)}}
.stock-title{{font-weight:700;margin-bottom:7px}}
.path{{display:flex;align-items:center;gap:7px;flex-wrap:wrap;margin:5px 0}}
.arrow{{color:#92999f}}
a{{color:var(--link);text-decoration:none}}
a:hover{{text-decoration:underline}}
.unavailable{{color:var(--muted)}}
.tabs{{display:flex;gap:4px;border-bottom:1px solid var(--line);margin-top:24px}}
.tab{{border:0;border-radius:6px 6px 0 0;background:transparent;padding:10px 15px;font-size:14px;cursor:pointer;color:#50565c}}
.tab.active{{background:#e7edf3;color:#173c67;font-weight:700}}
.level-section[hidden]{{display:none}}
.filter{{margin:16px 0 10px;max-width:420px}}
.table-wrap{{overflow-x:auto;border:1px solid var(--line);border-radius:6px}}
table{{width:100%;border-collapse:collapse;min-width:820px}}
th,td{{padding:10px 12px;border-bottom:1px solid var(--line);text-align:left;font-size:13px;vertical-align:top}}
th{{background:var(--soft);font-weight:700}}
tr:last-child td{{border-bottom:0}}
.number{{text-align:right;white-space:nowrap}}
.status-ok{{color:var(--ok)}}
.status-missing{{color:var(--warn)}}
.notice{{border-left:3px solid #b98a17;padding-left:12px;margin-top:18px}}
@media(max-width:640px){{.wrap{{padding:18px 14px}}h1{{font-size:21px}}.summary{{gap:10px 16px}}}}
</style>
</head>
<body>
<header><div class="wrap">
<h1>策略一：申万行业近期低点反弹</h1>
<p>{display_date} · 最近 {window} 个交易日窗口</p>
<div class="summary">
<span><strong>{level_counts['L1']}</strong> 个 L1，{chart_counts['L1']} 个有图</span>
<span><strong>{level_counts['L2']}</strong> 个 L2，{chart_counts['L2']} 个有图</span>
<span><strong>{level_counts['L3']}</strong> 个 L3，{chart_counts['L3']} 个有图</span>
</div>
</div></header>
<main class="wrap">
<section class="search">
<h2>个股行业查询</h2>
<input id="stockSearch" type="search" placeholder="股票代码或名称" autocomplete="off">
<div id="stockResults" aria-live="polite"></div>
</section>
<p class="notice">个股归属使用字典当前的 SW2021 成分关系，不代表历史日期上的行业归属；同一股票存在多条归属记录时全部展示。</p>
<div class="tabs" role="tablist">
<button class="tab active" data-level="L1" type="button">L1 一级行业</button>
<button class="tab" data-level="L2" type="button">L2 二级行业</button>
<button class="tab" data-level="L3" type="button">L3 三级行业</button>
</div>
{sections}
</main>
<script id="stockData" type="application/json">{stock_json}</script>
<script>
const stocks=JSON.parse(document.getElementById('stockData').textContent);
const stockInput=document.getElementById('stockSearch');
const stockResults=document.getElementById('stockResults');
const esc=(value)=>String(value).replace(/[&<>"']/g,(c)=>({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[c]));
function industryLink(item){{
  const label=`${{esc(item.level)}} ${{esc(item.name)}} (${{esc(item.code)}})`;
  return item.href?`<a href="${{encodeURI(item.href)}}">${{label}}</a>`:`<span class="unavailable">${{label}} · 无缓存行情</span>`;
}}
function renderStocks(){{
  const query=stockInput.value.trim().toLowerCase();
  if(!query){{stockResults.innerHTML='';return;}}
  const matches=stocks.filter((item)=>item.code.toLowerCase().includes(query)||item.symbol.toLowerCase().includes(query)||item.name.toLowerCase().includes(query)).slice(0,30);
  if(!matches.length){{stockResults.innerHTML='<p>没有匹配的股票。</p>';return;}}
  stockResults.innerHTML=matches.map((item)=>{{
    const paths=item.paths.length?item.paths.map((path)=>`<div class="path">${{path.map(industryLink).join('<span class="arrow">→</span>')}}</div>`).join(''):'<div class="unavailable">当前字典中没有申万行业归属</div>';
    return `<div class="stock-result"><div class="stock-title">${{esc(item.name||'-')}} · ${{esc(item.code)}}</div>${{paths}}</div>`;
  }}).join('');
}}
stockInput.addEventListener('input',renderStocks);
document.querySelectorAll('.tab').forEach((button)=>button.addEventListener('click',()=>{{
  document.querySelectorAll('.tab').forEach((item)=>item.classList.toggle('active',item===button));
  document.querySelectorAll('.level-section').forEach((section)=>section.hidden=section.dataset.level!==button.dataset.level);
}}));
document.querySelectorAll('.industry-filter').forEach((input)=>input.addEventListener('input',()=>{{
  const query=input.value.trim().toLowerCase();
  const section=input.closest('.level-section');
  section.querySelectorAll('tbody tr').forEach((row)=>row.hidden=!row.dataset.search.includes(query));
}}));
</script>
</body>
</html>
"""
    path = output / "index.html"
    path.write_text(html, encoding="utf-8")
    return path


def generate_industry_rebound_charts(
    *,
    as_of_date: str | None = None,
    window: int = 20,
    dictionary_path: str | Path = DEFAULT_DICTIONARY_PATH,
    cache_root: str | Path | None = None,
    output_directory: str | Path | None = None,
    initial_bars: int | None = 120,
) -> dict[str, Any]:
    """Generate all cached L1/L2/L3 charts and the static browse page."""
    if window < 1:
        raise ValueError("window must be at least 1")
    catalog, stock_paths = load_industry_catalog(dictionary_path)
    bars = load_cached_industry_bars(as_of_date=as_of_date, cache_root=cache_root)
    if bars.empty:
        raise ValueError("no valid cached industry bars found")
    latest_date = str(bars["trade_date"].max())
    node_by_code = {node["code"]: node for node in catalog}
    bars = bars[bars["index_code"].isin(node_by_code)].copy()
    if bars.empty:
        raise ValueError("cached industry bars do not match the SW dictionary")

    bars["name"] = bars["index_code"].map(
        {code: node["name"] for code, node in node_by_code.items()}
    )
    metric_input = bars.copy()
    metric_input["level"] = metric_input["index_code"].map(
        {code: node["level"] for code, node in node_by_code.items()}
    )
    metrics = calculate_industry_index_screen(
        metric_input,
        as_of_date=latest_date,
        window=window,
    )
    metric_by_code = {
        str(row["index_code"]): row
        for row in metrics.to_dict("records")
    }

    output = (
        Path(output_directory)
        if output_directory is not None
        else DEFAULT_OUTPUT_ROOT
        / f"{latest_date[:4]}-{latest_date[4:6]}-{latest_date[6:8]}"
        / "charts"
    )
    output.mkdir(parents=True, exist_ok=True)
    available_codes: set[str] = set()
    errors: list[str] = []
    grouped = {str(code): group for code, group in bars.groupby("index_code")}
    for node in catalog:
        code = node["code"]
        index_bars = grouped.get(code)
        if index_bars is None or index_bars.empty:
            continue
        try:
            write_industry_rebound_html(
                index_bars,
                output / _chart_filename(node["level"], code),
                index_code=code,
                index_name=node["name"],
                level=node["level"],
                metric=metric_by_code.get(code),
                window=window,
                initial_bars=initial_bars,
            )
            available_codes.add(code)
        except ValueError as exc:
            errors.append(f"{code}: {exc}")

    root = Path(cache_root) if cache_root is not None else DEFAULT_CACHE_ROOT
    basic_path = root / "static" / "stock_basic.parquet"
    stock_basic = pd.read_parquet(basic_path) if basic_path.exists() else pd.DataFrame()
    stock_records = build_stock_search_records(
        stock_basic,
        stock_paths,
        available_codes,
    )
    index_path = write_industry_chart_index(
        output,
        catalog=catalog,
        stock_records=stock_records,
        available_codes=available_codes,
        metric_by_code=metric_by_code,
        as_of_date=latest_date,
        window=window,
    )
    missing = [
        {"index_code": node["code"], "name": node["name"], "level": node["level"]}
        for node in catalog
        if node["code"] not in available_codes
    ]
    counts = {
        level: {
            "total": sum(node["level"] == level for node in catalog),
            "charts": sum(
                node["level"] == level and node["code"] in available_codes
                for node in catalog
            ),
        }
        for level in LEVEL_LABELS
    }
    return {
        "index": str(index_path),
        "output_directory": str(output),
        "as_of_date": latest_date,
        "window": window,
        "counts": counts,
        "chart_count": len(available_codes),
        "missing_count": len(missing),
        "missing": missing,
        "errors": errors,
    }


def _industry_ref(code: str, node: Mapping[str, Any], level: str) -> dict[str, str]:
    return {"code": str(code), "name": str(node.get("name", "")), "level": level}


def _catalog_node(
    industry: dict[str, str],
    path: list[dict[str, str]],
    stock_codes: list[str] | None = None,
) -> dict[str, Any]:
    return {
        **industry,
        "path": [dict(item) for item in path],
        "stock_codes": stock_codes or [],
    }


def _chart_filename(level: str, code: str) -> str:
    return f"{level}_{code.replace('.', '_')}.html"


def _render_level_section(
    level: str,
    catalog: list[dict[str, Any]],
    available: set[str],
    metric_by_code: Mapping[str, Mapping[str, Any]],
    *,
    hidden: bool,
) -> str:
    rows = []
    for node in catalog:
        if node["level"] != level:
            continue
        metric = metric_by_code.get(node["code"], {})
        breadcrumb = " → ".join(item["name"] for item in node["path"][:-1]) or "-"
        search = escape(
            " ".join(
                [node["code"], node["name"]]
                + [item["name"] for item in node["path"]]
            ).lower(),
            quote=True,
        )
        if node["code"] in available:
            chart = (
                f'<a href="{escape(_chart_filename(level, node["code"]))}">查看图表</a>'
            )
            status = '<span class="status-ok">有缓存行情</span>'
        else:
            chart = '<span class="unavailable">-</span>'
            status = '<span class="status-missing">无缓存行情</span>'
        rows.append(
            f'<tr data-search="{search}">'
            f"<td>{escape(node['name'])}</td>"
            f"<td>{escape(node['code'])}</td>"
            f"<td>{escape(breadcrumb)}</td>"
            f'<td class="number">{_format_pct(metric.get("rise_from_low_pct"))}</td>'
            f'<td class="number">{_format_number(metric.get("days_since_low"), 0)}</td>'
            f"<td>{status}</td>"
            f"<td>{chart}</td>"
            "</tr>"
        )
    return f"""<section class="level-section" data-level="{level}"{' hidden' if hidden else ''}>
<div class="filter"><input class="industry-filter" type="search" placeholder="筛选行业名称或代码" autocomplete="off"></div>
<div class="table-wrap"><table>
<thead><tr><th>行业</th><th>指数代码</th><th>上级路径</th><th class="number">窗口低点涨幅</th><th class="number">低点后天数</th><th>行情</th><th>图表</th></tr></thead>
<tbody>{''.join(rows)}</tbody>
</table></div>
</section>"""


def _metric_summary(metric: Mapping[str, Any] | pd.Series, window: int) -> str:
    if metric is None or len(metric) == 0 or str(metric.get("data_quality", "")) != "ok":
        return f"最近 {window} 个交易日窗口 · 历史记录不足，策略指标不可用"
    return (
        f"最近 {window} 日窗口：{_display_date(_metric_text(metric, 'window_start_date'))} 起 · "
        f"低点 {_display_date(_metric_text(metric, 'window_low_date'))} / "
        f"{_format_number(metric.get('window_low_close'), 2)} · "
        f"低点后 {_format_number(metric.get('days_since_low'), 0)} 个交易日 · "
        f"反弹 {_format_pct(metric.get('rise_from_low_pct'))} · "
        f"上涨/下跌 {_format_number(metric.get('up_days_after_low'), 0)}/"
        f"{_format_number(metric.get('down_days_after_low'), 0)} 天"
    )


def _metric_text(metric: Mapping[str, Any] | pd.Series, key: str) -> str:
    value = metric.get(key)
    if value is None or pd.isna(value):
        return ""
    return str(value)


def _format_pct(value: Any) -> str:
    if value is None or pd.isna(value):
        return "-"
    return f"{float(value):.2%}"


def _format_number(value: Any, digits: int) -> str:
    if value is None or pd.isna(value):
        return "-"
    return f"{float(value):,.{digits}f}"


def _display_date(value: str) -> str:
    text = _normalise_date(value)
    if not text:
        return "-"
    return f"{text[:4]}-{text[4:6]}-{text[6:8]}"


def _normalise_date(value: Any) -> str:
    if value is None or pd.isna(value):
        return ""
    text = str(value).strip().replace("-", "")
    return text[:8] if len(text) >= 8 and text[:8].isdigit() else ""


def _import_plotly() -> tuple[Any, Any]:
    try:
        import plotly.graph_objects as go
        from plotly.subplots import make_subplots
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise RuntimeError(
            'Plotly is required for charts. Install it with `pip install -e ".[viz]"`.'
        ) from exc
    return go, make_subplots
