'use strict';
// All numbers and paths below are returned by the existing offline engine.
const bt = {catalog:null, history:[], selected:[], current:null, comparisons:new Map(), busy:false,
  error:'', deletePending:null, search:'', name:'', start_date:'', end_date:'', monthly_amount:'3000', calendar_day:5,
  rebalance:'revalidated_target_gap', loading:false};
const btColors=['#117b68','#bf8461','#657eab','#9278a5','#c29b40','#559da1'];
const btPct=v=>v==null?'—':`${Number(v).toFixed(2)}%`;
async function btRequest(path,body){
  const r=await fetch(path,body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const result=await r.json();
  if(!r.ok)throw Error(result?.error||'本地服务暂时无法完成请求');
  return result;
}
async function btDeleteRequest(id){
  const r=await fetch(`/api/backtests?id=${encodeURIComponent(id)}`,{method:'DELETE'}),result=await r.json();
  if(!r.ok)throw Error(result?.error||'删除实验失败');
  return result;
}
async function btLoad(){
  if(bt.loading)return;
  bt.loading=true;
  try{const [catalog,history]=await Promise.all([btRequest('/api/backtests/catalog'),btRequest('/api/backtests')]);bt.catalog=catalog;bt.history=history;}
  catch(e){bt.error=e.message;bt.catalog=bt.catalog||[];}
  finally{bt.loading=false;if(page==='backtests')render();}
}
function btSearch(){
  const items=(bt.catalog||[]).filter(f=>!bt.selected.some(x=>x.code===f.code)&&`${f.code} ${f.name}`.toLowerCase().includes(bt.search.toLowerCase())).slice(0,15);
  return items.map(f=>`<button type="button" data-bt-add="${esc(f.code)}"><span>${esc(f.name)} <small>${esc(f.code)}</small></span><small>${esc(f.start_date)} — ${esc(f.end_date)}　＋</small></button>`).join('')||'<p class="muted">没有可添加的基金。这里只提供通过校验的本地净值。</p>';
}
function btWeights(){const sum=bt.selected.reduce((s,f)=>s+Number(f.weight||0),0);return `目标比例合计 <strong>${sum.toFixed(2)}%</strong> · ${Math.abs(sum-100)<1e-8?'可以运行':'需调整到 100%'}`;}
function btReady(){return bt.selected.length>0&&bt.selected.every(f=>Number(f.weight)>0)&&Math.abs(bt.selected.reduce((s,f)=>s+Number(f.weight||0),0)-100)<1e-8;}
function backtests(){
  if(bt.catalog===null){queueMicrotask(btLoad);return panel('策略回测',empty('正在读取本地净值与实验…'));}
  const rows=bt.selected.map(f=>{const item=bt.catalog.find(x=>x.code===f.code);return `<tr><td>${esc(item?.name||f.code)}<small>${esc(f.code)} · ${esc(item?.start_date)} — ${esc(item?.end_date)}</small></td><td><input aria-label="${esc(f.code)} 目标比例" data-bt-fund="${f.code}" data-field="weight" type="number" min="0.01" max="100" step="0.01" value="${esc(f.weight)}" required> %</td><td><input aria-label="${esc(f.code)} 研究申购费率" data-bt-fund="${f.code}" data-field="fee_pct" type="number" min="0" max="10" step="0.0001" value="${esc(f.fee_pct)}" required> %</td><td><button type="button" data-bt-remove="${f.code}" ${bt.busy?'disabled':''}>移除</button></td></tr>`;});
  return `<section class="intro"><div><h2>用历史数据，验证自己的配置。</h2><p>每月定投 · 新增资金再平衡 · 保存并比较实验</p></div><span class="pill">研究实验 · 不改变持仓</span></section>
  <div class="notice">复用本地回测引擎。V1 不卖出、不借款；交易日与确认日采用保守研究假设，结果不代表可直接执行的渠道订单。</div>
  <section class="panel bt-config"><h2>实验配置</h2><form id="bt-form"><fieldset ${bt.busy?'disabled':''}>
    <div class="bt-fields"><label>实验名称<input data-bt-key="name" value="${esc(bt.name)}" maxlength="80" placeholder="例如：全球均衡 · 每月 3000"></label><label>每月定投 / 元<input data-bt-key="monthly_amount" type="number" min="0.01" max="1000000" step="0.01" required value="${esc(bt.monthly_amount)}"></label><label>开始日期<input data-bt-key="start_date" type="date" required value="${esc(bt.start_date)}"></label><label>结束日期<input data-bt-key="end_date" type="date" required value="${esc(bt.end_date)}"></label><label>每月定投日期<select data-bt-key="calendar_day">${Array.from({length:28},(_,i)=>`<option value="${i+1}" ${Number(bt.calendar_day)===i+1?'selected':''}>每月 ${i+1} 日</option>`).join('')}</select></label><label>再平衡方式<select data-bt-key="rebalance"><option value="revalidated_target_gap" ${bt.rebalance==='revalidated_target_gap'?'selected':''}>新增资金优先补低配</option><option value="fixed_weights" ${bt.rebalance==='fixed_weights'?'selected':''}>新增资金按固定比例</option></select></label></div>
    <h3>基金与目标比例</h3><input id="bt-search" aria-label="搜索可回测基金" value="${esc(bt.search)}" placeholder="搜索名称或六位代码"><details class="bt-picker" ${bt.search?'open':''}><summary>添加基金 · ${bt.catalog.length} 只有本地历史</summary><div id="bt-search-results">${btSearch()}</div></details>
    ${rows.length?table(['基金 / 可用历史','目标比例','研究申购费率','操作'],rows):empty('先添加基金，再填写比例与费用。')}
    <div class="split"><p id="bt-weight-status" role="status">${btWeights()}</p><button type="submit" class="bt-primary" ${!btReady()?'disabled':''}>${bt.busy?'正在计算…':'执行回测 →'}</button></div></fieldset></form>
    <details><summary>净值、交易日、确认日与费用规则</summary><p>月度预算在所选自然日确定，非净值日顺延到各基金下一可用净值日；使用申购日单位净值成交，下一条基金净值记录日作为可见日和确认日。分配只使用下单前已可见的净值，不使用未来收益。净值日期是研究交易日代理，并非核验的渠道交易日历。</p><p>费用由你显式填写，0% 是零费假设；采用比例前端扣费，管理/托管费已在净值中。不核验历史限额，假设最低 1 元、无限额、份额保留两位向下取整。剩余金额留在现金，现金不计息；现金分红下一净值日到账。只计算完整估值日，不前向填充缺失净值。起始和期末估值可能与请求日期不同。</p><p>组合净值扣除新增投入的影响，是引擎时间加权路径，首个完整估值日为 1；首日前费用体现在累计收益和 XIRR 中。最大回撤基于这条净值路径。XIRR 无解时显示 —。研究结果不能自动进入实盘。</p></details>
  </section><div id="bt-error" role="alert">${bt.error?`<div class="notice bad">${esc(bt.error)}</div>`:''}</div>
  ${bt.current?btResult(bt.current):''}${btHistory()}${btCompare()}`;
}
function btProfile(e){return e.result.profile_results[0];}
function btChart(series,label){
  const valid=series.filter(s=>s.points.length>1);
  if(!valid.length)return empty('暂无足够的完整估值点');
  const points=valid.flatMap(s=>s.points),dates=points.map(p=>Date.parse(p.date+'T00:00:00Z')),values=points.map(p=>Number(p.value));
  const minDate=Math.min(...dates),maxDate=Math.max(...dates),lo=Math.min(...values),hi=Math.max(...values),range=hi-lo||Math.max(1,Math.abs(lo)*.01);
  const x=d=>70+(Date.parse(d+'T00:00:00Z')-minDate)/(maxDate-minDate||1)*650,y=v=>210-(Number(v)-lo)/range*170;
  return `<svg class="chart bt-chart" viewBox="0 0 750 250" role="img" aria-label="${esc(label)}">${[0,.5,1].map(t=>`<path d="M70 ${210-t*170}H720" stroke="#e2e9e6"/><text x="60" y="${214-t*170}" text-anchor="end">${(lo+t*range).toLocaleString('zh-CN',{maximumFractionDigits:2})}</text>`).join('')}${valid.map((s,i)=>`<polyline points="${s.points.map(p=>`${x(p.date)},${y(p.value)}`).join(' ')}" fill="none" stroke="${btColors[i%btColors.length]}" stroke-width="2.5"><title>${esc(s.name)}</title></polyline>`).join('')}<text x="70" y="242">${new Date(minDate).toISOString().slice(0,10)}</text><text x="720" y="242" text-anchor="end">${new Date(maxDate).toISOString().slice(0,10)}</text></svg><div class="bt-legend">${valid.map((s,i)=>`<span><i style="background:${btColors[i%btColors.length]}"></i>${esc(s.name)}</span>`).join('')}</div>`;
}
function btResult(e){
  const p=btProfile(e),m=p.metrics,daily=p.valuation_result.daily_valuations,last=daily.at(-1),principal=Number(m.contributions_cny),profit=Number(m.final_value_cny)-principal;
  const positions=last.positions||[],total=Number(last.total_value_cny),cash=Number(last.available_cash_cny)+Number(last.pending_subscription_cash_cny)+Number(last.distribution_receivable_cny);
  const slices=positions.map(f=>({name:(bt.catalog.find(x=>x.code===f.fund_code)?.name||f.fund_code),value:Number(f.market_value_cny)}));if(cash>0)slices.push({name:'现金 / 待确认 / 分红应收',value:cash});
  let angle=0;const gradient=slices.map((f,i)=>{const start=angle;angle+=total?f.value/total*360:0;return `${btColors[i%btColors.length]} ${start}deg ${angle}deg`;}).join(',');
  return `<section class="panel"><div class="split"><h2>${esc(e.name)}</h2><div class="actions"><button data-bt-save ${bt.busy?'disabled':''}>保存实验</button><button data-bt-current-compare>加入对比</button></div></div><p class="muted">实际完整估值：${esc(m.start_date)} — ${esc(m.end_date)} · ${m.valuation_observations} 个观察值 · 输入与净值批次已绑定</p><div class="metrics">${metric('累计投入本金',`¥ ${money(principal)}`,'按月外部现金流')}${metric('期末资产',`¥ ${money(m.final_value_cny)}`,'基金 + 可用现金 + 待确认 + 分红应收')}${metric('累计收益',`<span class="${profit>=0?'positive':'negative'}">${signedMoney(profit)}</span>`,principal?`简单收益率 ${btPct(profit/principal*100)}`:'—')}${metric('XIRR',btPct(m.xirr_pct),'资金加权年化收益')}${metric('最大回撤',btPct(m.maximum_drawdown_pct),'组合时间加权净值')}${metric('时间加权收益',btPct(m.time_weighted_return_pct),`申购费 ¥ ${money(m.purchase_fees_cny)}`)}</div><div class="grid">${panel('资产总值曲线',btChart([{name:e.name,points:daily.map(r=>({date:r.visible_date,value:r.total_value_cny}))}],'资产总值曲线'))}${panel('投资组合净值曲线',btChart([{name:e.name,points:m.unitized_path}],'组合净值曲线'))}</div><h3>期末持仓占比（含现金）</h3><div class="bt-holdings"><div class="bt-donut" role="img" aria-label="期末持仓占比" style="background:conic-gradient(${gradient||'#e2e9e6 0deg 360deg'})"><span>期末配置</span></div>${table(['基金 / 资产','市值 / 元','资产占比'],slices.map((f,i)=>`<tr><td><i class="bt-dot" style="background:${btColors[i%btColors.length]}"></i>${esc(f.name)}</td><td>${money(f.value)}</td><td>${btPct(total?f.value/total*100:0)}</td></tr>`))}</div><p class="muted">可用现金 ¥ ${money(last.available_cash_cny)} · 待确认 ¥ ${money(last.pending_subscription_cash_cny)} · 分红应收 ¥ ${money(last.distribution_receivable_cny)} · 跳过不完整估值 ${m.skipped_valuations} 次 · 拒绝订单 ${m.rejected_subscriptions} 笔。曲线仅连接完整观察值，不代表缺失日已有估值。</p><details><summary>月度分配、模拟订单与数据依据</summary><pre>${esc(JSON.stringify({config:e.config,engine:e.result.engine_version,scenario_sha256:e.result.scenario_sha256,data_bindings:e.result.data_bindings,monthly:p.monthly_reconciliation,subscriptions:p.subscription_result},null,2))}</pre></details></section>`;
}
function btHistory(){return panel('历史实验',`<div class="split"><p class="muted">保留原始配置和计算结果；加载后可修改参数并重跑。</p><button data-bt-reload>刷新历史</button></div>${bt.history.length?table(['实验名称','期间 / 月投入','XIRR / 最大回撤','操作'],bt.history.map(e=>{const pending=bt.deletePending?.id===e.id;return `<tr><td>${esc(e.name)}<small>${time(e.created_at)}</small></td><td>${esc(e.config.start_date)} — ${esc(e.config.end_date)}<small>¥ ${money(e.config.monthly_amount)} / 月</small></td><td>${btPct(e.metrics.xirr_pct)} / ${btPct(e.metrics.maximum_drawdown_pct)}</td><td>${pending?`<div class="bt-delete-confirm" role="alert"><span>永久删除，无法恢复</span><div class="bt-row-actions"><button class="bt-danger-solid" data-bt-confirm-delete="${e.id}">确认删除</button><button data-bt-cancel-delete>取消</button></div></div>`:`<div class="bt-row-actions"><button data-bt-load="${e.id}">加载</button><button data-bt-compare="${e.id}">加入对比</button><button class="bt-danger" data-bt-delete="${e.id}" data-bt-delete-name="${esc(e.name)}">删除</button></div>`}</td></tr>`;})):empty('尚未保存实验。完成计算后，填写名称并点击“保存实验”。')}`);}
function btCompare(){
  if(!bt.comparisons.size)return '';
  const items=[...bt.comparisons.values()];
  return panel('实验对比',`<p class="muted">按真实日历日期叠加。投入金额、日期或期间不同会影响 XIRR 和资产规模，比较前请核对配置；净值各自从首个完整估值日归一化。</p>${table(['实验','期间 / 月投入 / 日期','累计本金','期末资产','累计收益','XIRR','最大回撤','操作'],items.map(e=>{const m=btProfile(e).metrics;return `<tr><td>${esc(e.name)}</td><td>${esc(e.config.start_date)} — ${esc(e.config.end_date)}<small>¥ ${money(e.config.monthly_amount)} · 每月 ${e.config.calendar_day} 日 · ${e.config.rebalance==='fixed_weights'?'固定比例':'补低配'}</small></td><td>${money(m.contributions_cny)}</td><td>${money(m.final_value_cny)}</td><td>${money(Number(m.final_value_cny)-Number(m.contributions_cny))}</td><td>${btPct(m.xirr_pct)}</td><td>${btPct(m.maximum_drawdown_pct)}</td><td><button data-bt-uncompare="${e.id}">移除</button></td></tr>`;}))}${btChart(items.map(e=>({name:e.name,points:btProfile(e).metrics.unitized_path})),'多实验组合净值叠加')}`);
}
function btAddCompare(e){if(bt.comparisons.size>=6&&!bt.comparisons.has(e.id))throw Error('一次最多对比 6 组实验');bt.comparisons.set(e.id,e);}
document.addEventListener('input',e=>{
  const t=e.target;
  if(t.id==='bt-search'){bt.search=t.value;$('#bt-search-results').innerHTML=btSearch();$('.bt-picker').open=true;}
  if(t.dataset.btKey)bt[t.dataset.btKey]=['calendar_day'].includes(t.dataset.btKey)?Number(t.value):t.value;
  if(t.dataset.btFund){const f=bt.selected.find(f=>f.code===t.dataset.btFund);if(f){f[t.dataset.field]=t.value;$('#bt-weight-status').innerHTML=btWeights();$('#bt-form [type="submit"]').disabled=!btReady();}}
});
document.addEventListener('change',e=>{if(e.target.dataset.btKey)bt[e.target.dataset.btKey]=e.target.dataset.btKey==='calendar_day'?Number(e.target.value):e.target.value;});
document.addEventListener('submit',async e=>{
  if(e.target.id!=='bt-form')return;e.preventDefault();if(bt.busy)return;
  bt.busy=true;bt.error='';render();
  try{const result=await btRequest('/api/backtests/run',{start_date:bt.start_date,end_date:bt.end_date,monthly_amount:bt.monthly_amount,calendar_day:Number(bt.calendar_day),rebalance:bt.rebalance,funds:bt.selected});bt.current={...result,name:bt.name.trim()||'未命名实验'};}
  catch(error){bt.error=error.message;}
  finally{bt.busy=false;if(page==='backtests')render();}
});
document.addEventListener('click',async e=>{
  const b=e.target.closest('button');if(!b)return;const d=b.dataset;
  if(!Object.keys(d).some(k=>k.startsWith('bt')))return;
  if(bt.busy)return;
  bt.error='';
  try{
    if(d.btAdd){if(bt.selected.length>=10)throw Error('最多选择 10 只基金');bt.selected.push({code:d.btAdd,weight:bt.selected.length?'':'100',fee_pct:'0'});const chosen=bt.selected.map(x=>bt.catalog.find(f=>f.code===x.code));const min=chosen.map(f=>f.start_date).sort().at(-1),max=chosen.map(f=>f.end_date).sort()[0];if(!bt.start_date||bt.start_date<min)bt.start_date=min;if(!bt.end_date||bt.end_date>max)bt.end_date=max;}
    if(d.btRemove)bt.selected=bt.selected.filter(f=>f.code!==d.btRemove);
    if('btSave' in d){if(!bt.name.trim())throw Error('请先在配置中填写实验名称');bt.busy=true;bt.current=await btRequest('/api/backtests/save',{run_id:bt.current.id,name:bt.name});bt.history=await btRequest('/api/backtests');toast('实验已保存到本地。');}
    if(d.btLoad){const saved=await btRequest(`/api/backtests?id=${d.btLoad}`);bt.current=saved;Object.assign(bt,saved.config);bt.selected=saved.config.funds.map(f=>({...f}));bt.name=saved.name;}
    if(d.btCompare)btAddCompare(await btRequest(`/api/backtests?id=${d.btCompare}`));
    if(d.btDelete)bt.deletePending={id:d.btDelete,name:d.btDeleteName||'这个实验'};
    if('btCancelDelete' in d)bt.deletePending=null;
    if(d.btConfirmDelete){
      bt.busy=true;await btDeleteRequest(d.btConfirmDelete);
      bt.history=bt.history.filter(item=>item.id!==d.btConfirmDelete);
      bt.comparisons.delete(d.btConfirmDelete);
      if(bt.current?.id===d.btConfirmDelete)bt.current=null;
      bt.deletePending=null;
      toast('历史实验已删除。');
    }
    if('btCurrentCompare' in d)btAddCompare(bt.current);
    if(d.btUncompare)bt.comparisons.delete(d.btUncompare);
    if('btReload' in d){bt.catalog=null;btLoad();}
  }catch(error){bt.error=error.message;}
  finally{bt.busy=false;if(page==='backtests')render();}
});
