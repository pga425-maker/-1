/* ---------- 결과 분석 ---------- */

const FACTOR = { base:'종목 자체 흐름', event:'뉴스 이벤트', fear:'공포지수', fx:'환율' };
const PAIRING = { 차트추세:'momentum', 저평가:'contrarian', 뉴스:'news', 분산:'hold',
                  실적성장:'mixed', 직감:'mixed' };

function allAccounts(){ return [G.player].concat(G.bots); }
function retOf(acc){ return assetOf(acc, G.prices) / G.sc.starting_cash - 1; }

/* 행동 기반 성향. 서버판 analysis.classify_behavior 와 같은 기준을 쓴다. */
function classifyBehavior(acc){
  const buys = acc.buys || [];
  if (!buys.length) return { type:'mixed', label:BEHAVIOR_LABEL.mixed, votes:{} };
  const headlineTicks = G.events.filter(e => e.headline).map(e => e.tick);
  const votes = { momentum:0, contrarian:0, news:0 };
  for (const b of buys){
    if (headlineTicks.some(h => b.tick - h >= 0 && b.tick - h <= 4)){ votes.news++; continue; }
    const h = G.history[b.symbol];
    const idx = Math.min(b.tick, h.length - 1);
    const past = h[Math.max(0, idx - 5)];
    const trend = past ? h[idx]/past - 1 : 0;
    if (trend > 0.004) votes.momentum++;
    else if (trend < -0.004) votes.contrarian++;
  }
  const trades = acc.trades || [];
  const earlyCut = G.total * 0.35;
  if (trades.length <= 10 && trades.every(o => o.tick <= earlyCut))
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
  const by = {};
  for (const o of G.sells.concat(open)){
    const k = o.reason || '미기재';
    by[k] = by[k] || { reason:k, count:0, invested:0, profit:0, closed:0 };
    by[k].count += 1;
    by[k].invested += o.qty * o.buy;
    by[k].profit += o.qty * (o.exit - o.buy);
  }
  return Object.values(by)
    .map(r => ({ ...r, avg: r.invested ? r.profit / r.invested : 0 }))
    .sort((a,b) => b.count - a.count);
}

function typeAverages(){
  const by = {};
  for (const acc of allAccounts()){
    const k = classifyBehavior(acc).type;
    (by[k] = by[k] || []).push(retOf(acc));
  }
  return Object.keys(by).map(k => ({
    type: k, label: BEHAVIOR_LABEL[k], count: by[k].length,
    avg: by[k].reduce((a,b) => a+b, 0) / by[k].length,
  })).sort((a,b) => b.avg - a.avg);
}

const CROWD_SHOW = 4;

function crowdReport(){
  return G.crowd.map(m => {
    const foll = [], others = [];
    for (const acc of allAccounts()){
      (m.followers.has(acc) ? foll : others).push(retOf(acc));
    }
    const avg = a => a.length ? a.reduce((x,y) => x+y, 0) / a.length : 0;
    return {
      symbol: m.symbol, name: G.stockBy[m.symbol].name,
      from: m.from, to: m.to, count: foll.length,
      follower: avg(foll), other: avg(others),
      iFollowed: m.followers.has(G.player),
    };
  });
}

/* 쏠림 구간이 열 개 넘게 잡히면 화면이 벽이 된다.
   참여자가 많았던 순으로 몇 개만 보여 주고 나머지는 숫자로 요약한다. */
function topCrowd(rows){
  return rows.slice()
    .sort((a, b) => (b.iFollowed ? 1 : 0) - (a.iFollowed ? 1 : 0) || b.count - a.count)
    .slice(0, CROWD_SHOW);
}

function shortReport(){
  return G.events.filter(e => e.type === 'short_pressure').map(e => {
    const h = G.history[e.symbol];
    const at = i => h[Math.min(Math.max(i,0), h.length-1)];
    const log = G.player.trades;
    const bought = log.some(o => o.side==='buy' && o.symbol===e.symbol
      && o.tick>=e.pumpStart && o.tick<=e.pumpEnd+1);
    const sold = log.some(o => o.side==='sell' && o.symbol===e.symbol
      && o.tick>e.pumpEnd && o.tick<=e.end+1);
    const entrants = G.bots.filter(b => (b.trades||[]).some(o =>
      o.side==='buy' && o.symbol===e.symbol && o.tick>=e.pumpStart && o.tick<=e.pumpEnd+1));
    const escaped = entrants.filter(b => (b.trades||[]).some(o =>
      o.side==='sell' && o.symbol===e.symbol && o.tick>e.pumpEnd && o.tick<=e.end+1));
    return {
      headline: e.headline, symbol: e.symbol, name: G.stockBy[e.symbol].name,
      pump: at(e.pumpStart-1) ? at(e.pumpEnd)/at(e.pumpStart-1)-1 : 0,
      dump: at(e.pumpEnd) ? at(e.end)/at(e.pumpEnd)-1 : 0,
      bought, sold, entrants: entrants.length, escaped: escaped.length,
    };
  });
}

function bestRecord(ret){
  try {
    const raw = localStorage.getItem('moui.best');
    const prev = raw === null ? null : Number(raw);
    if (prev === null || ret > prev){ localStorage.setItem('moui.best', String(ret)); return { prev, isNew: prev !== null }; }
    return { prev, isNew: false };
  } catch (e) { return { prev: null, isNew: false }; }
}

function renderResult(){
  const p = G.player, asset = assetOf(p, G.prices);
  const ret = asset / G.sc.starting_cash - 1;
  const me = G.rank.find(r => r.me) || {};
  const c = G.contrib;
  const parts = [['base',c.base],['event',c.event],['fear',c.fear],['fx',c.fx]];
  const maxAbs = Math.max(1, ...parts.map(([,v]) => Math.abs(v)));
  const reasons = reasonReport();
  const beh = classifyBehavior(p);
  const declared = (() => {
    const cnt = {};
    p.buys.forEach(b => { if (b.reason) cnt[b.reason] = (cnt[b.reason]||0)+1; });
    const keys = Object.keys(cnt);
    return keys.length ? keys.reduce((a,k) => cnt[k] > cnt[a] ? k : a, keys[0]) : null;
  })();
  const gap = declared && beh.type !== 'mixed' && PAIRING[declared] && PAIRING[declared] !== beh.type;
  const types = typeAverages();
  const crowd = crowdReport();
  const shorts = shortReport();
  const best = bestRecord(ret);

  const sec = (title, inner) => `<div style="margin-top:18px">
    <div style="font-size:14px;font-weight:700;margin-bottom:8px">${title}</div>${inner}</div>`;

  app.innerHTML = `<div class="wrap plain">
    <div style="padding-top:22px">
      <div class="muted" style="font-size:13px">${esc(p.name)} 님의 결과</div>
      <div class="display" style="font-size:32px;margin-top:2px">${me.rankReturn || '―'}위
        <span class="muted" style="font-size:15px;margin-left:8px">/ ${G.rank.length}명</span></div>
      ${best.isNew ? `<div class="up" style="font-size:12px;font-weight:700;margin-top:4px">
        이전 최고 기록 ${pct(best.prev)}을 넘었습니다</div>`
        : (best.prev !== null ? `<div class="muted" style="font-size:12px;margin-top:4px">
        내 최고 기록 ${pct(best.prev)}</div>` : '')}
    </div>

    <div class="card" style="margin-top:14px;border-radius:20px;padding:20px">
      <div class="row between"><span class="muted" style="font-size:12px">최종 자산</span>
        <span class="display num" style="font-size:24px">${won(asset)}</span></div>
      <div class="row between" style="margin-top:8px"><span class="muted" style="font-size:12px">수익률</span>
        <span class="num ${cls(ret)}" style="font-size:16px;font-weight:700">${pct(ret)}</span></div>
      <div class="row" style="gap:8px;margin-top:14px">
        ${[['위험조정', riskScore(G.stats).toFixed(3)],
           ['최대낙폭', (G.stats.mdd*100).toFixed(1)+'%'],
           ['종합 순위', (me.rankTotal||'―')+'위']].map(([k,v]) =>
          `<div class="metric" style="flex:1"><div class="k">${k}</div><div class="v num">${v}</div></div>`).join('')}
      </div>
      <div class="muted" style="font-size:11px;margin-top:10px">
        위험조정은 틱별 수익률의 평균을 표준편차로 나눈 값입니다.
        같은 수익이면 덜 흔들린 쪽이 높습니다</div>
    </div>

    ${sec('수익이 어디서 왔는가', `<div class="card">
      ${parts.map(([k,v]) => `<div style="padding:7px 0">
        <div class="row between"><span style="font-size:12px">${FACTOR[k]}</span>
          <span class="num ${cls(v)}" style="font-size:12px;font-weight:700">${signedWon(v)}</span></div>
        <div class="bar"><i style="width:${Math.abs(v)/maxAbs*50}%;background:${v>0?'var(--up)':'var(--down)'};
          ${v<0?'transform:translateX(-100%)':''}"></i></div></div>`).join('')}
      <div class="muted" style="font-size:11px;margin-top:8px">
        들고 있던 동안의 가격 변동만 나눈 것입니다. 매매 타이밍과 수수료는 빠져 있습니다</div>
    </div>`)}

    ${sec('매수 근거별 성적', `<div class="card">${reasons.length ? reasons.map(r => `<div class="kv">
      <span><span style="font-size:13px;font-weight:700">${esc(r.reason)}</span><br>
        <span class="muted num" style="font-size:11px">${r.count}번 · ${won(r.invested)}원 투입</span></span>
      <span class="num ${cls(r.avg)}" style="font-size:14px;font-weight:700">${pct(r.avg)}</span></div>`).join('')
      : '<div class="muted" style="font-size:12px">매수 기록이 없습니다</div>'}</div>`)}

    ${sec('투자 성향 진단', `<div class="card">
      <div class="row between"><span class="muted" style="font-size:12px">내가 고른 근거</span>
        <span style="font-size:13px;font-weight:700">${declared ? esc(declared) : '―'}</span></div>
      <div class="row between" style="margin-top:8px"><span class="muted" style="font-size:12px">실제 매매 패턴</span>
        <span style="font-size:13px;font-weight:700">${beh.label}</span></div>
      ${gap ? `<div style="margin-top:12px;background:var(--bg);border-radius:12px;padding:10px 12px;
        font-size:12px;line-height:1.5">스스로 ${esc(declared)}${josa(declared,'을','를')} 가장 많이
        골랐으나 실제 매매는 ${beh.label}에 가까웠습니다</div>` : ''}
      <div style="margin-top:14px">
        <div class="muted" style="font-size:11px;margin-bottom:6px">이번 판 유형별 평균 수익률</div>
        ${types.map(t => `<div class="row between" style="padding:5px 0">
          <span style="font-size:12px${t.type===beh.type?';font-weight:700':''}">${t.label}
            <span class="muted">${t.count}명</span>${t.type===beh.type?' <span class="tag">나</span>':''}</span>
          <span class="num ${cls(t.avg)}" style="font-size:12px;font-weight:700">${pct(t.avg)}</span>
        </div>`).join('')}
      </div>
    </div>`)}

    ${sec('군중을 따라갔을 때', `<div class="card">${crowd.length ? `
      ${crowd.length > CROWD_SHOW ? `<div class="muted" style="font-size:11px;margin-bottom:8px">
        한 종목으로 몰린 구간이 ${crowd.length}번 있었습니다.
        그중 참여자가 많았던 ${CROWD_SHOW}개를 봅니다</div>` : ''}
      ${topCrowd(crowd).map(m => `
      <div class="kv" style="display:block">
        <div style="font-size:12px;font-weight:700">${esc(m.name)} 쏠림
          <span class="muted" style="font-weight:400">${m.count}명 참여</span>
          ${m.iFollowed?'<span class="tag" style="margin-left:4px">나도 샀음</span>':''}</div>
        <div class="row between" style="margin-top:5px">
          <span class="muted" style="font-size:11px">따라간 사람 평균</span>
          <span class="num ${cls(m.follower)}" style="font-size:12px;font-weight:700">${pct(m.follower)}</span></div>
        <div class="row between">
          <span class="muted" style="font-size:11px">안 따라간 사람 평균</span>
          <span class="num ${cls(m.other)}" style="font-size:12px;font-weight:700">${pct(m.other)}</span></div>
      </div>`).join('')}`
      : '<div class="muted" style="font-size:12px">이번 판에는 한 종목으로 뚜렷하게 몰린 구간이 없었습니다</div>'}</div>`)}

    ${shorts.length ? sec('공매도 집중 구간', `<div class="card">${shorts.map(s => `
      <div class="kv" style="display:block">
        <div style="font-size:12px;font-weight:700">${esc(s.headline)}</div>
        <div class="muted num" style="font-size:11px;margin-top:3px">
          펌프 ${pct(s.pump)} 뒤 덤프 ${pct(s.dump)}</div>
        <div style="font-size:12px;margin-top:6px;font-weight:700;color:${
          s.bought && !s.sold ? 'var(--up)' : 'var(--ink)'}">${
          s.bought ? (s.sold ? '펌프 구간에 샀지만 덤프 전에 빠져나왔습니다'
                             : '펌프 구간에 사서 덤프를 그대로 맞았습니다')
                   : '이 구간에는 사지 않았습니다'}</div>
        <div class="muted" style="font-size:11px;margin-top:3px">
          다른 참가자 ${s.entrants}명이 펌프 구간에 들어갔고 그중 ${s.escaped}명이 덤프 전에 나왔습니다</div>
      </div>`).join('')}</div>`) : ''}

    ${sec('내 주문 이력', `<div class="card">
      ${p.trades.length ? `<div style="max-height:280px;overflow-y:auto">
        ${p.trades.slice().reverse().map(o => `<div class="kv">
          <span><span style="font-size:12px;font-weight:700;color:${o.side==='buy'?'var(--up)':'var(--down)'}">
            ${o.side==='buy'?'매수':'매도'}</span>
            <span style="font-size:12px"> ${esc(G.stockBy[o.symbol].name)}</span><br>
            <span class="muted num" style="font-size:11px">${o.tick}틱${o.reason?' · '+esc(o.reason):''}</span></span>
          <span class="num" style="font-size:12px;font-weight:700">${o.qty}주 @ ${won(o.price)}</span>
        </div>`).join('')}</div>`
        : '<div class="muted" style="font-size:12px">주문 기록이 없습니다</div>'}
    </div>`)}

    <div class="note" style="margin-top:18px">
      경고 배지는 펌프 구간에도 계속 경고 톤을 유지합니다. 오르는 중이라고 안심시키면
      "지금 사도 되나"라는 잘못된 신호가 되어 교육 목적이 무너집니다.
    </div>
    <button id="again" class="cta" style="margin-top:18px">다시 하기</button>
  </div>`;
}
