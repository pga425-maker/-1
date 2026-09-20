/* ---------- 결과 ---------- */

const FACTOR = { base:'종목 자체 흐름', event:'뉴스 이벤트', fear:'공포지수', fx:'환율' };
const PAIRING = { 차트추세:'momentum', 저평가:'contrarian', 뉴스:'news', 분산:'hold',
                  실적성장:'mixed', 직감:'mixed' };

function classifyBehavior(){
  if (!G.buys.length) return { type:'mixed', label:BEHAVIOR_LABEL.mixed };
  const headlineTicks = G.events.filter(e => e.headline).map(e => e.tick);
  const votes = { momentum:0, contrarian:0, news:0 };
  for (const b of G.buys){
    if (headlineTicks.some(h => b.tick - h >= 0 && b.tick - h <= 4)){ votes.news++; continue; }
    const h = G.history[b.symbol];
    const idx = Math.min(b.tick, h.length - 1);
    const past = h[Math.max(0, idx - 5)];
    const trend = past ? h[idx]/past - 1 : 0;
    if (trend > 0.004) votes.momentum++;
    else if (trend < -0.004) votes.contrarian++;
  }
  const allTrades = G.orderLog;
  const earlyCut = G.total * 0.35;
  if (allTrades.length <= 10 && allTrades.every(o => o.tick <= earlyCut))
    return { type:'hold', label:BEHAVIOR_LABEL.hold, votes };
  const sum = votes.momentum + votes.contrarian + votes.news;
  let kind = 'mixed';
  if (sum){
    kind = Object.keys(votes).reduce((a,c) => votes[c] > votes[a] ? c : a, 'momentum');
    if (votes[kind] / sum < 0.55) kind = 'mixed';
  }
  return { type:kind, label:BEHAVIOR_LABEL[kind], votes };
}

function reasonReport(){
  const open = [];
  for (const s in G.lots){
    for (const lot of G.lots[s]){
      if (lot.qty > 0) open.push({ reason: lot.reason, qty: lot.qty, buy: lot.price, exit: G.prices[s] });
    }
  }
  const all = G.sells.concat(open);
  const by = {};
  for (const o of all){
    const k = o.reason || '미기재';
    by[k] = by[k] || { reason:k, count:0, invested:0, profit:0 };
    by[k].count += 1;
    by[k].invested += o.qty * o.buy;
    by[k].profit += o.qty * (o.exit - o.buy);
  }
  return Object.values(by)
    .map(r => ({ ...r, avg: r.invested ? r.profit / r.invested : 0 }))
    .sort((a,b) => b.count - a.count);
}

function shortReport(){
  return G.events.filter(e => e.type === 'short_pressure').map(e => {
    const h = G.history[e.symbol];
    const at = i => h[Math.min(Math.max(i,0), h.length-1)];
    const boughtInPump = G.orderLog.some(o =>
      o.side==='buy' && o.symbol===e.symbol && o.tick>=e.pumpStart && o.tick<=e.pumpEnd+1);
    const soldInDump = G.orderLog.some(o =>
      o.side==='sell' && o.symbol===e.symbol && o.tick>e.pumpEnd && o.tick<=e.end+1);
    return {
      headline: e.headline, symbol: e.symbol,
      pump: at(e.pumpStart-1) ? at(e.pumpEnd)/at(e.pumpStart-1)-1 : 0,
      dump: at(e.pumpEnd) ? at(e.end)/at(e.pumpEnd)-1 : 0,
      boughtInPump, soldInDump,
    };
  });
}

function renderResult(){
  const p = G.player, asset = assetOf(p, G.prices);
  const me = G.rank.find(r => r.me) || {};
  const c = G.contrib;
  const parts = [['base',c.base],['event',c.event],['fear',c.fear],['fx',c.fx]];
  const maxAbs = Math.max(1, ...parts.map(([,v]) => Math.abs(v)));
  const reasons = reasonReport();
  const beh = classifyBehavior();
  const declared = (() => {
    const cnt = {};
    G.buys.forEach(b => { if (b.reason) cnt[b.reason] = (cnt[b.reason]||0)+1; });
    const keys = Object.keys(cnt);
    return keys.length ? keys.reduce((a,k) => cnt[k] > cnt[a] ? k : a, keys[0]) : null;
  })();
  const gap = declared && beh.type !== 'mixed' && PAIRING[declared] && PAIRING[declared] !== beh.type;
  const shorts = shortReport();

  app.innerHTML = `<div class="wrap plain">
    <div style="padding-top:22px">
      <div class="muted" style="font-size:13px">${esc(p.name)} 님의 결과</div>
      <div class="display" style="font-size:32px;margin-top:2px">${me.rankReturn || '―'}위
        <span class="muted" style="font-size:15px;margin-left:8px">/ ${G.rank.length}명</span></div>
    </div>
    <div class="card" style="margin-top:14px;border-radius:20px;padding:20px">
      <div class="row between"><span class="muted" style="font-size:12px">최종 자산</span>
        <span class="display num" style="font-size:24px">${won(asset)}</span></div>
      <div class="row between" style="margin-top:8px"><span class="muted" style="font-size:12px">수익률</span>
        <span class="num ${cls(asset-G.sc.starting_cash)}" style="font-size:16px;font-weight:700">
          ${pct(asset/G.sc.starting_cash-1)}</span></div>
      <div class="row" style="gap:8px;margin-top:14px">
        ${[['위험조정', riskScore(G.stats).toFixed(3)],
           ['최대낙폭', (G.stats.mdd*100).toFixed(1)+'%'],
           ['종합 순위', (me.rankTotal||'―')+'위']].map(([k,v]) =>
          `<div class="metric" style="flex:1"><div class="k">${k}</div><div class="v num">${v}</div></div>`).join('')}
      </div>
    </div>

    <div style="margin-top:18px"><div style="font-size:14px;font-weight:700;margin-bottom:8px">수익이 어디서 왔는가</div>
    <div class="card">
      ${parts.map(([k,v]) => `<div style="padding:7px 0">
        <div class="row between"><span style="font-size:12px">${FACTOR[k]}</span>
          <span class="num ${cls(v)}" style="font-size:12px;font-weight:700">${signedWon(v)}</span></div>
        <div class="bar"><i style="width:${Math.abs(v)/maxAbs*50}%;background:${v>0?'var(--up)':'var(--down)'};
          ${v<0?'transform:translateX(-100%)':''}"></i></div></div>`).join('')}
      <div class="muted" style="font-size:11px;margin-top:8px">
        들고 있던 동안의 가격 변동만 나눈 것입니다. 매매 타이밍과 수수료는 빠져 있습니다</div>
    </div></div>

    <div style="margin-top:18px"><div style="font-size:14px;font-weight:700;margin-bottom:8px">매수 근거별 성적</div>
    <div class="card">${reasons.length ? reasons.map(r => `<div class="kv">
      <span><span style="font-size:13px;font-weight:700">${esc(r.reason)}</span><br>
        <span class="muted num" style="font-size:11px">${r.count}번 · ${won(r.invested)}원 투입</span></span>
      <span class="num ${cls(r.avg)}" style="font-size:14px;font-weight:700">${pct(r.avg)}</span></div>`).join('')
      : '<div class="muted" style="font-size:12px">매수 기록이 없습니다</div>'}</div></div>

    <div style="margin-top:18px"><div style="font-size:14px;font-weight:700;margin-bottom:8px">투자 성향 진단</div>
    <div class="card">
      <div class="row between"><span class="muted" style="font-size:12px">내가 고른 근거</span>
        <span style="font-size:13px;font-weight:700">${declared ? esc(declared) : '―'}</span></div>
      <div class="row between" style="margin-top:8px"><span class="muted" style="font-size:12px">실제 매매 패턴</span>
        <span style="font-size:13px;font-weight:700">${beh.label}</span></div>
      ${gap ? `<div style="margin-top:12px;background:var(--bg);border-radius:12px;padding:10px 12px;
        font-size:12px;line-height:1.5">스스로 ${esc(declared)}${josa(declared,'을','를')} 가장 많이
        골랐으나 실제 매매는 ${beh.label}에 가까웠습니다</div>` : ''}
    </div></div>

    ${shorts.length ? `<div style="margin-top:18px">
      <div style="font-size:14px;font-weight:700;margin-bottom:8px">공매도 집중 구간</div>
      <div class="card">${shorts.map(s => `<div class="kv" style="display:block">
        <div style="font-size:12px;font-weight:700">${esc(s.headline)}</div>
        <div class="muted num" style="font-size:11px;margin-top:3px">
          펌프 ${pct(s.pump)} 뒤 덤프 ${pct(s.dump)}</div>
        <div style="font-size:12px;margin-top:6px;font-weight:700;color:${
          s.boughtInPump && !s.soldInDump ? 'var(--up)' : 'var(--ink)'}">${
          s.boughtInPump
            ? (s.soldInDump ? '펌프 구간에 샀지만 덤프 전에 빠져나왔습니다'
                            : '펌프 구간에 사서 덤프를 그대로 맞았습니다')
            : '이 구간에는 사지 않았습니다'}</div></div>`).join('')}</div>
    </div>` : ''}

    <div class="note" style="margin-top:18px">
      경고 배지는 펌프 구간에도 계속 경고 톤을 유지합니다. 오르는 중이라고 안심시키면
      "지금 사도 되나"라는 잘못된 신호가 되기 때문입니다.
    </div>
    <button id="again" class="cta" style="margin-top:18px">다시 하기</button>
  </div>`;
}

/* ---------- 라우팅 ---------- */

function render(){
  if (screen === 'start') return renderStart();
  if (screen === 'home') return renderHome();
  if (screen === 'market') return renderMarket();
  if (screen === 'news') return renderNews();
  if (screen === 'rank') return renderRank();
  if (screen === 'detail') return renderDetail();
  if (screen === 'result') return renderResult();
}

function goDetail(sym){
  detailSymbol = sym; screen = 'detail';
  side = 'buy'; qty = 1; reason = null;
  render();
}

app.addEventListener('click', (ev) => {
  const t = ev.target.closest('[data-tab],[data-stock],[data-go],[data-rank],[data-sort],[data-side],[data-q],[data-reason],#back,#order,#again,#maxBtn');
  if (!t) return;

  if (t.dataset.tab){ screen = t.dataset.tab; return render(); }
  if (t.dataset.go) return goDetail(t.dataset.go);
  if (t.dataset.stock) return goDetail(t.dataset.stock);
  if (t.dataset.rank){ rankTab = t.dataset.rank; return render(); }
  if (t.dataset.sort){ sortBy = t.dataset.sort; return render(); }
  if (t.id === 'back'){ screen = 'home'; return render(); }
  if (t.id === 'again'){ clearInterval(timer); screen = 'start'; return render(); }

  if (t.dataset.side){ side = t.dataset.side; qty = 1; reason = null; return render(); }
  if (t.dataset.q){ qty = Math.max(1, qty + Number(t.dataset.q)); return render(); }
  if (t.dataset.reason){ reason = t.dataset.reason; return render(); }
  if (t.id === 'maxBtn'){
    const m = side === 'buy'
      ? maxBuyable(G, G.player, detailSymbol)
      : (G.player.holdings[detailSymbol] || 0) - (G.player.reservedQty[detailSymbol] || 0);
    if (m > 0){ qty = m; render(); }
    return;
  }
  if (t.id === 'order'){
    const res = submit(G, G.player, detailSymbol, side, qty, side === 'buy' ? reason : null);
    toast(res.msg);
    if (res.ok){ qty = 1; reason = null; }
    return render();
  }
});

app.addEventListener('input', (ev) => {
  if (ev.target.id === 'qty'){
    const n = parseInt(String(ev.target.value).replace(/\D/g, ''), 10);
    qty = Number.isFinite(n) && n > 0 ? n : 1;
  }
});

window.addEventListener('resize', () => {
  if (screen === 'detail' && G){
    const st = G.stockBy[detailSymbol];
    drawChart(G.history[detailSymbol], G.prices[detailSymbol] >= st.initial_price);
  }
});

render();
