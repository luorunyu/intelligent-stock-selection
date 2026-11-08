"""Offline day selection and price-level chip-change panel for strategy charts."""

from __future__ import annotations

import json
from typing import Any


def chip_detail_script(payload: dict[str, Any], bars: list[dict[str, Any]]) -> str:
    data = json.dumps({"chips": payload, "bars": bars}, ensure_ascii=False, allow_nan=False).replace("</", "<\\/")
    return _SCRIPT.replace("__CHIP_DATA__", data)


_SCRIPT = r"""
(function () {
  const gd = document.getElementById('{plot_id}');
  const source = __CHIP_DATA__;
  const bars = Object.fromEntries(source.bars.map(bar => [bar.date, bar]));
  const dates = source.bars.map(bar => bar.date);
  const style = document.createElement('style');
  style.textContent = `
    body{margin:10px;font-family:Arial,'PingFang SC','Microsoft YaHei',sans-serif;color:#243247}
    .chip-workspace{display:flex;gap:12px;align-items:flex-start}
    .chip-main{flex:1;min-width:0}.chip-main>.plotly-graph-div{width:100%!important}
    .chip-aside{width:365px;flex:none;position:sticky;top:10px;border:1px solid #e0e6ef;border-radius:10px;background:#f8fafc;padding:14px;box-sizing:border-box}
    .chip-aside h2{font-size:17px;margin:0 0 10px}.chip-hint{font-size:12px;line-height:1.7;color:#66758c}
    .chip-controls{display:flex;gap:6px;align-items:center;margin:10px 0}
    .chip-controls input{min-width:0;flex:1}.chip-controls button,.chip-controls input{padding:6px;border:1px solid #cdd6e3;border-radius:5px;background:white}
    .chip-status{font-size:13px;line-height:1.7;min-height:44px}.chip-plot{height:390px}
    @media(max-width:1000px){.chip-workspace{display:block}.chip-aside{width:100%;position:static;margin-top:12px}.chip-plot{height:320px}}
  `;
  document.head.appendChild(style);
  const wrapper = document.createElement('div'); wrapper.className = 'chip-workspace';
  const main = document.createElement('div'); main.className = 'chip-main';
  const aside = document.createElement('aside'); aside.className = 'chip-aside';
  gd.parentNode.insertBefore(wrapper, gd); wrapper.appendChild(main); main.appendChild(gd); wrapper.appendChild(aside);
  aside.innerHTML = `<h2>当日筹码净变化</h2>
    <div class="chip-hint">点击任意一天的K线、成交量或标记，查看当天价格范围内的分布。</div>
    <div class="chip-controls"><button type="button" data-chip="previous">前一天</button><input type="date" aria-label="筹码日期"><button type="button" data-chip="next">后一天</button></div>
    <div class="chip-status" aria-live="polite"></div><div class="chip-plot"></div>
    <div class="chip-hint">净变化 = 今日筹码占比 − 前一交易日占比。<br>横轴单位：百分点；正值增加，负值减少。<br>仅保留当日最低价至最高价内的价档；不归一化、不加换手率修正。<br>筹码模型估算，不代表实际逐笔买入或资金净流入。</div>`;
  const picker = aside.querySelector('input');
  const previous = aside.querySelector('[data-chip="previous"]');
  const next = aside.querySelector('[data-chip="next"]');
  const status = aside.querySelector('.chip-status');
  const panel = aside.querySelector('.chip-plot');
  const iso = date => date.slice(0,4)+'-'+date.slice(4,6)+'-'+date.slice(6,8);
  picker.min = iso(dates[0]); picker.max = iso(dates[dates.length-1]);
  let selected = dates[dates.length-1];
  const plotConfig = {responsive:true,displayModeBar:false};
  const empty = () => Plotly.react(panel, [], {height:370,margin:{l:20,r:20,t:20,b:20},paper_bgcolor:'#f8fafc',plot_bgcolor:'#f8fafc',xaxis:{visible:false},yaxis:{visible:false}}, plotConfig);
  function selectDate(date) {
    const index = dates.indexOf(date);
    if(index < 0) {status.textContent = '所选日期没有K线，请选择交易日。';empty();return;}
    selected = date; picker.value = iso(date); previous.disabled = index === 0; next.disabled = index === dates.length-1;
    const bar = bars[date]; const item = source.chips[date];
    const shapes = (gd.layout.shapes || []).filter(shape => shape.name !== 'selected-chip-date');
    shapes.push({type:'line',name:'selected-chip-date',xref:'x',yref:'paper',x0:iso(date),x1:iso(date),y0:0,y1:1,line:{color:'#7b4bc4',width:1,dash:'dot'}});
    Plotly.relayout(gd, {shapes:shapes});
    if(!item) {status.textContent = iso(date)+'：暂无本地筹码净变化数据，未将缺失数据视为零。';empty();return;}
    if(item.status === 'EMPTY_RANGE') {status.textContent = iso(date)+'：当日价格范围内没有原始筹码价档。';empty();return;}
    const sum = item.net.reduce((a,b)=>a+b,0);
    status.textContent = iso(date)+' 对比 '+iso(item.previous_date)+'；范围 '+item.low.toFixed(2)+'–'+item.high.toFixed(2)+' 元；净变化合计 '+(sum>=0?'+':'')+sum.toFixed(3)+' 个百分点。';
    const peak = Math.max(...item.net.map(value=>Math.abs(value)),.01);
    const traces = [{type:'bar',orientation:'h',x:item.net,y:item.price,
      marker:{color:item.net.map(value=>value>=0?'#159b87':'#d35555')},
      text:item.net.map(value=>(value>=0?'+':'')+value.toFixed(3)),textposition:'outside',cliponaxis:false,
      hovertemplate:'价格 %{y:.2f}元<br>筹码净变化 %{x:+.4f} 个百分点<extra></extra>'}];
    Plotly.react(panel, traces, {
      height:370,margin:{l:62,r:58,t:20,b:46},paper_bgcolor:'#f8fafc',plot_bgcolor:'white',
      font:{family:'Arial, PingFang SC, Microsoft YaHei, sans-serif',size:11,color:'#243247'},
      xaxis:{title:{text:'筹码净变化（百分点）'},zeroline:true,zerolinecolor:'#94a3b8',range:[Math.min(0,...item.net)-peak*.08,Math.max(0,...item.net)+peak*.34]},
      yaxis:{title:{text:'价格（元）'},tickformat:'.2f',tickmode:item.price.length<=16?'array':'auto',tickvals:item.price,range:[item.low-.06,item.high+.06]},
      shapes:[{type:'line',xref:'paper',x0:0,x1:1,yref:'y',y0:bar.close,y1:bar.close,line:{color:'#64748b',dash:'dot',width:1}}]
    }, plotConfig);
  }
  gd.on('plotly_click', function(event) {
    const point = event.points && event.points[0];
    if(!point || point.x === undefined) return;
    const value = point.x instanceof Date ? point.x.toISOString().slice(0,10) : String(point.x).slice(0,10);
    const date = value.replace(/-/g,'');
    if(bars[date]) selectDate(date);
  });
  picker.addEventListener('change',()=>selectDate(picker.value.replace(/-/g,'')));
  previous.addEventListener('click',()=>selectDate(dates[Math.max(0,dates.indexOf(selected)-1)]));
  next.addEventListener('click',()=>selectDate(dates[Math.min(dates.length-1,dates.indexOf(selected)+1)]));
  const resize = () => {Plotly.Plots.resize(gd);if(panel.data) Plotly.Plots.resize(panel);};
  window.addEventListener('resize',resize);
  selectDate(selected);
  setTimeout(resize,0);
})();
"""
