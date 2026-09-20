/* ===================== 화면 ===================== */

const app = document.getElementById('app');
const toastEl = document.getElementById('toast');
let G = null, timer = null, screen = 'start', detailSymbol = null;
let side = 'buy', qty = 1, reason = null, rankTab = 'return', sortBy = 'default';
let speed = 1.2, nickname = '', paused = false;
// 근거 줄 강조가 살아 있어야 하는 시각. 틱마다 다시 그려도 지워지지 않게 상태로 둔다.
let askUntil = 0;
let toastTimer = null;

const SPEEDS = [
  { s: 4.8, label: '실제 속도', sub: '30분' },
  { s: 1.2, label: '빠르게', sub: '7분 30초' },
  { s: 0.4, label: '아주 빠르게', sub: '2분 30초' },
];

const esc = (s) => String(s).replace(/[&<>"']/g, c =>
  ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const won = (v) => Math.round(Number(v) || 0).toLocaleString('ko-KR');
const pct = (v, d = 1) => {
  const n = (Number(v) || 0) * 100;
  const r = Number(n.toFixed(d));
  if (r === 0) return (0).toFixed(d) + '%';
  return (r > 0 ? '+' : '') + r.toFixed(d) + '%';
};
const signedWon = (v) => {
  const n = Math.round(Number(v) || 0);
  const m = n > 0 ? '▲ ' : n < 0 ? '▼ ' : '';
  return m + Math.abs(n).toLocaleString('ko-KR') + '원';
};
const cls = (v) => v > 0 ? 'up' : v < 0 ? 'down' : 'muted';
const clock = (sec) => {
  const s = Math.max(0, Math.floor(sec));
  return String(Math.floor(s / 60)).padStart(2, '0') + ':' + String(s % 60).padStart(2, '0');
};
const arrow = (v) => {
  const n = Math.round(v);
  if (n === 0) return '―';
  return (n > 0 ? '▲' : '▼') + Math.abs(n);
};
/* 한국어 조사. 받침 유무로 을/를, 이/가 를 고른다. */
const josa = (word, withBatchim, without) => {
  const last = String(word).trim().slice(-1);
  const code = last.charCodeAt(0);
  if (code < 0xAC00 || code > 0xD7A3) return without;
  return (code - 0xAC00) % 28 ? withBatchim : without;
};

function toast(msg){
  toastEl.textContent = msg;
  toastEl.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { toastEl.hidden = true; }, 2200);
}

const SVG = {
  clock: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#FBF7F0" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 1.8"/></svg>',
  back: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#211A2E" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M15 5l-7 7 7 7"/></svg>',
  chev: '<svg class="chev" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 5l7 7-7 7"/></svg>',
  alert: '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="#C13B3B" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="flex-shrink:0;margin-top:1px"><path d="M12 4.5 21 19.5H3z"/><path d="M12 10v4"/><path d="M12 16.8v.2"/></svg>',
  speaker: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#211A2E" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 9.5h3.5L12 6v12l-4.5-3.5H4z"/><path d="M15.5 9.5a4 4 0 0 1 0 5"/></svg>',
  minus: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#211A2E" stroke-width="2" stroke-linecap="round"><path d="M6 12h12"/></svg>',
  plus: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#211A2E" stroke-width="2" stroke-linecap="round"><path d="M12 6v12"/><path d="M6 12h12"/></svg>',
  check: '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="#FBF7F0" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12.5 10 17l9-10"/></svg>',
  tab: {
    home: '<path d="M3 10.5 12 3l9 7.5"/><path d="M5.5 9.5V20h13V9.5"/><path d="M9.5 20v-5.5h5V20"/>',
    market: '<path d="M3 20h18"/><path d="M6 20v-6"/><path d="M11 20V8"/><path d="M16 20v-9"/><path d="M21 20V5"/>',
    news: '<path d="M4 5.5h13a1 1 0 0 1 1 1V19H5.5A1.5 1.5 0 0 1 4 17.5z"/><path d="M18 9h1.5A1.5 1.5 0 0 1 21 10.5v7A1.5 1.5 0 0 1 19.5 19H18"/><path d="M7.5 9h6"/><path d="M7.5 12.5h6"/><path d="M7.5 16h3.5"/>',
    rank: '<path d="M8 21h8"/><path d="M12 17v4"/><path d="M7 4h10v5a5 5 0 0 1-10 0z"/><path d="M7 5.5H4.5V7a3 3 0 0 0 3 3"/><path d="M17 5.5h2.5V7a3 3 0 0 1-3 3"/>',
  },
};
const tabIcon = (k, on) => `<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="${on ? '#211A2E' : '#5C5548'}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${SVG.tab[k]}</svg>`;

/* ---------- 조각 ---------- */

function tickerHTML(){
  return `<div class="ticker">
    <span>공포지수 <b class="num">${Math.round(G.fear[G.fear.length-1])}</b>
      <b class="${cls(G.disp.fear)}">${arrow(G.disp.fear)}</b></span>
    <span>원/달러 <b class="num">${won(G.fx[G.fx.length-1])}</b>
      <b class="${cls(G.disp.fx)}">${arrow(G.disp.fx)}</b></span>
  </div>`;
}
function bannerHTML(){
  const keys = Object.keys(G.badges);
  if (!keys.length) return '';
  return keys.map(sym => {
    const st = G.stockBy[sym];
    return `<button class="banner fade" data-go="${sym}">${SVG.alert}
      <span><span class="t">${esc(st.name)} · ${esc(G.badges[sym].label)}</span><br>
      <span class="s">공매도 집중으로 위험하지만, 일시적으로 오를 수 있습니다</span></span></button>`;
  }).join('');
}
function topbarHTML(title){
  const left = G.total - G.tick;
  return `<div class="topbar"><span class="title">${esc(title)}</span>
    <span class="row" style="gap:8px">
      <button id="pause" class="tag" style="padding:6px 11px;min-height:32px">
        ${paused ? '재개' : '일시정지'}</button>
      <span class="pill num">${SVG.clock}${clock(left * G.sc.tick_seconds)}</span>
    </span></div>`;
}
function tabbarHTML(active){
  const tabs = [['home','홈'],['market','시세'],['news','뉴스'],['rank','랭킹']];
  return `<nav class="tabbar"><div class="inner">${tabs.map(([k,l]) => {
    const on = active === k;
    return `<button data-tab="${k}" class="${on?'on':''}">${tabIcon(k,on)}<span>${l}</span></button>`;
  }).join('')}</div></nav>`;
}
function stockRow(s, sub, rightV, rightP, extra){
  return `<button class="item" data-stock="${s.symbol}">
    <span class="ava" style="background:${s.avatar_color}">${esc(s.name[0])}</span>
    <span style="min-width:0;flex:1">
      <span class="nm">${esc(s.name)}</span>${extra || ''}<br>
      <span class="sb">${sub}</span>
    </span>
    <span class="rt"><span class="v num">${rightV}</span><br>
      <span class="p num ${rightP.c}">${rightP.t}</span></span>${SVG.chev}</button>`;
}

/* ---------- 시작 ---------- */

function renderStart(){
  app.innerHTML = `<div class="wrap plain">
    <div style="height:72px"></div>
    <h1 class="display" style="font-size:30px;line-height:1.25;margin:0">모의투자 리그</h1>
    <p class="muted" style="font-size:13px;margin:8px 0 0">
      시작 자금 1,000만 원으로 가상 종목 7개에 투자합니다</p>
    <div class="note" style="margin-top:14px">
      가상 종목 7개와 뉴스 이벤트가 있는 30분짜리 시장에서 다른 참가자 15명과 겨룹니다.
      끝나면 수익률뿐 아니라 위험조정 점수, 매수 근거별 성적, 투자 성향까지 짚어 줍니다.
      실존 기업과 실제 주가 데이터는 쓰지 않습니다.
    </div>
    <label for="nick" class="muted" style="display:block;font-size:12px;margin-top:22px">닉네임</label>
    <input id="nick" maxlength="12" placeholder="12자 이내" autocomplete="off"
      style="width:100%;margin-top:8px;background:var(--card);border:1px solid var(--line);
             border-radius:14px;padding:15px 16px;font-size:16px;outline:none">
    <div class="muted" style="font-size:12px;margin-top:18px">진행 속도</div>
    <div class="speed" style="margin-top:8px">
      ${SPEEDS.map(x => `<button data-speed="${x.s}" class="${x.s===speed?'on':''}">
        ${x.label}<br><span style="font-size:10px;opacity:.75">한 판 ${x.sub}</span></button>`).join('')}
    </div>
    <div class="muted" style="font-size:11px;margin-top:8px">
      실제 축제에서는 4.8초 틱으로 30분간 진행합니다. 처음 해 보는 사람이 판단할
      시간을 두려고 일부러 느리게 잡은 값입니다.
    </div>
    <button id="startBtn" class="cta" style="margin-top:20px">시작하기</button>
  </div>`;
  const input = document.getElementById('nick');
  input.value = nickname;
  input.addEventListener('input', e => { nickname = e.target.value; });
  input.addEventListener('keydown', e => { if (e.key === 'Enter') start(); });
  document.getElementById('startBtn').addEventListener('click', start);
  app.querySelectorAll('[data-speed]').forEach(b =>
    b.addEventListener('click', () => { speed = Number(b.dataset.speed); renderStart(); }));
}

function start(){
  const name = (nickname || '').trim();
  if (!name){ toast('닉네임을 입력해 주세요'); document.getElementById('nick').focus(); return; }
  nickname = name;
  G = createGame(SCENARIO, (Date.now() % 100000) + 1, name);
  paused = false;
  refreshRank(G);
  screen = 'home';
  try { history.pushState({ screen: 'home' }, ''); } catch (e) { /* 무시 */ }
  render();
  startTimer();
}
function startTimer(){
  clearInterval(timer);
  timer = setInterval(() => {
    step(G);
    if (G.ended){ clearInterval(timer); screen = 'result'; }
    render();
  }, speed * 1000);
}

/* ---------- 홈 ---------- */

function renderHome(){
  const p = G.player, prices = G.prices;
  const asset = assetOf(p, prices);
  const profit = asset - G.sc.starting_cash;
  const me = G.rank.find(r => r.me);
  const sorted = G.symbols.slice().sort((a,b) =>
    prices[b]/G.stockBy[b].initial_price - prices[a]/G.stockBy[a].initial_price);
  const best = sorted[0], worst = sorted[sorted.length-1];
  const held = Object.keys(p.holdings);

  const slices = held.map(s => ({
    label: G.stockBy[s].name, value: p.holdings[s]*prices[s], color: G.stockBy[s].avatar_color }))
    .concat([{ label: '현금', value: p.cash + p.reserved, color: '#EFEAE0' }])
    .filter(x => x.value > 0);
  const totalV = slices.reduce((a,b) => a+b.value, 0);
  const top = slices.filter(s => s.label !== '현금').sort((a,b)=>b.value-a.value)[0];

  app.innerHTML = `<div class="wrap">
    ${topbarHTML('모의투자 리그')}${tickerHTML()}${bannerHTML()}
    <div class="asset">
      <div class="lab">내 총자산</div>
      <div class="big num">${won(asset)}</div>
      <div class="chg num ${cls(profit)}">${signedWon(profit)} (${pct(asset/G.sc.starting_cash-1)})</div>
      <div class="sub">전체 ${me ? me.rankReturn : '―'}위 · ${G.bots.length+1}명 참가</div>
      <div class="cash">현금 보유액 <b class="num" style="color:var(--ink)">${won(p.cash)}원</b>
        ${p.reserved > 0 ? ` · 주문 대기 <b class="num" style="color:var(--ink)">${won(p.reserved)}원</b>` : ''}</div>
    </div>

    <div class="card" style="margin-top:10px">
      <div class="row" style="gap:16px">
        ${donutHTML(slices, totalV)}
        <div style="flex:1;min-width:0">
          ${slices.slice(0,4).map(s => `<div class="row between" style="gap:8px;padding:2px 0">
            <span class="row" style="gap:6px;min-width:0">
              <i style="width:8px;height:8px;border-radius:3px;background:${s.color};flex-shrink:0"></i>
              <span class="muted truncate" style="font-size:11px">${esc(s.label)}</span></span>
            <b class="num" style="font-size:11px">${Math.round(s.value/totalV*100)}%</b></div>`).join('')}
          ${top && top.value/totalV >= 0.4 ? `<div class="up" style="font-size:11px;font-weight:700;margin-top:6px">
            ${esc(top.label)} 비중이 ${Math.round(top.value/totalV*100)}% 입니다</div>` : ''}
        </div>
      </div>
    </div>

    <div class="sechead"><span class="h">보유 종목</span><span class="r">평가금 · 등락률</span></div>
    <div class="list">
      ${held.length ? held.map(s => {
        const st = G.stockBy[s], q = p.holdings[s], v = q*prices[s];
        const pnl = p.avgCost[s] ? prices[s]/p.avgCost[s]-1 : 0;
        const badge = s===best ? '<span class="tag" style="margin-left:6px">오늘 1위</span>'
                    : s===worst ? '<span class="tag" style="margin-left:6px">오늘 꼴찌</span>' : '';
        return stockRow(st, `${q}주 · ${esc(st.sector)}`, won(v), {t:pct(pnl),c:cls(pnl)}, badge);
      }).join('') : '<div class="muted" style="text-align:center;font-size:12px;padding:26px 0">아직 보유한 종목이 없습니다. 시세 탭에서 골라 보세요</div>'}
    </div>
  </div>${tabbarHTML('home')}`;
}

function donutHTML(slices, total){
  const size = 86, r = size/2 - 7, C = 2*Math.PI*r;
  let off = 0;
  const arcs = slices.map(s => {
    const dash = (total ? s.value/total : 0) * C;
    const el = `<circle cx="${size/2}" cy="${size/2}" r="${r}" fill="none" stroke="${s.color}"
      stroke-width="13" stroke-dasharray="${dash} ${C-dash}" stroke-dashoffset="${-off}"/>`;
    off += dash; return el;
  }).join('');
  return `<svg width="${size}" height="${size}" viewBox="0 0 ${size} ${size}"
    style="transform:rotate(-90deg);flex-shrink:0" aria-hidden="true">${arcs}</svg>`;
}

/* ---------- 시세 ---------- */

function renderMarket(){
  const prices = G.prices;
  const chg = s => prices[s]/G.stockBy[s].initial_price - 1;
  const byChg = G.symbols.slice().sort((a,b) => chg(b)-chg(a));
  const list = sortBy === 'change' ? byChg : G.symbols;
  const card = (sym, label) => {
    const st = G.stockBy[sym], c = chg(sym), net = (G.flow[sym]||{}).net || 0;
    return `<button class="card" data-stock="${sym}" style="text-align:left;flex:1;min-width:0">
      <div class="muted" style="font-size:11px">${label}</div>
      <div class="row" style="gap:8px;margin-top:8px">
        <span class="ava" style="width:30px;height:30px;border-radius:9px;font-size:12px;background:${st.avatar_color}">${esc(st.name[0])}</span>
        <span class="truncate" style="font-size:13px;font-weight:700">${esc(st.name)}</span></div>
      <div class="display num ${cls(c)}" style="font-size:22px;margin-top:8px">${pct(c)}</div>
      <div class="muted num" style="font-size:11px">${won(prices[sym])}원</div>
      <div class="muted" style="font-size:11px;margin-top:6px">${
        net > 0 ? '지금 순매수 우세' : net < 0 ? '지금 순매도 우세' : '지금은 조용합니다'}</div>
    </button>`;
  };
  app.innerHTML = `<div class="wrap">
    ${topbarHTML('시세')}${tickerHTML()}${bannerHTML()}
    <div class="row" style="gap:10px;align-items:stretch">${card(byChg[0],'상승 1위')}${card(byChg[byChg.length-1],'하락 1위')}</div>
    <div class="row" style="gap:6px;padding:16px 0 8px">
      <button data-sort="default" class="${sortBy==='default'?'on':''}"
        style="border-radius:12px;padding:8px 14px;font-size:12px;font-weight:700;min-height:34px;
        background:${sortBy==='default'?'var(--ink)':'var(--card)'};color:${sortBy==='default'?'var(--bg)':'var(--muted)'}">기본순</button>
      <button data-sort="change" class="${sortBy==='change'?'on':''}"
        style="border-radius:12px;padding:8px 14px;font-size:12px;font-weight:700;min-height:34px;
        background:${sortBy==='change'?'var(--ink)':'var(--card)'};color:${sortBy==='change'?'var(--bg)':'var(--muted)'}">등락률순</button>
    </div>
    <div class="list">${list.map(s => {
      const st = G.stockBy[s], c = chg(s), net = (G.flow[s]||{}).net || 0;
      const sub = net > 0 ? `${esc(st.sector)} · 순매수 우세` : `${esc(st.sector)} · ${won(prices[s])}원`;
      return stockRow(st, sub, won(prices[s]), {t:pct(c),c:cls(c)});
    }).join('')}</div>
  </div>${tabbarHTML('market')}`;
}

/* ---------- 뉴스 ---------- */

function stateOf(e){ return G.tick < e.start ? 'pending' : (G.tick <= e.end ? 'applying' : 'done'); }
const STATE_LABEL = { pending:'반영 대기', applying:'반영중', done:'반영 완료' };

function renderNews(){
  const elapsed = e => {
    const sec = (G.tick - e.tick) * G.sc.tick_seconds;
    return sec < 60 ? '방금' : Math.floor(sec/60) + '분 전';
  };
  app.innerHTML = `<div class="wrap">
    ${topbarHTML('시장 속보')}
    <div class="row" style="gap:10px;align-items:stretch">
      ${[['공포지수', Math.round(G.fear[G.fear.length-1]), G.disp.fear],
         ['원/달러', won(G.fx[G.fx.length-1]), G.disp.fx]].map(([l,v,d]) =>
        `<div class="card" style="flex:1;border-radius:14px;padding:12px 14px">
           <div class="muted" style="font-size:11px">${l}</div>
           <div class="row" style="gap:6px;align-items:baseline;margin-top:2px">
             <span class="display num" style="font-size:19px">${v}</span>
             <span class="num ${cls(d)}" style="font-size:12px;font-weight:700">${arrow(d)}</span></div>
         </div>`).join('')}
    </div>
    <div class="list" style="margin-top:14px">
      ${G.news.length ? G.news.map(e => {
        const sp = e.type === 'short_pressure';
        const st = stateOf(e);
        return `<article class="news ${sp?'sp':''} fade">
          <div class="row" style="gap:8px">
            <span class="speaker ${sp?'warn':''}">${sp?SVG.alert:SVG.speaker}</span>
            <span style="font-size:11px;font-weight:700;color:${sp?'var(--up)':'var(--ink)'}">${sp?'경고':'속보'}</span>
            <span class="muted" style="font-size:11px;margin-left:auto">${elapsed(e)}</span></div>
          <div class="hd">${esc(e.headline)}</div>
          ${e.body ? `<div class="muted" style="font-size:12px">${esc(e.body)}</div>` : ''}
          <div class="row" style="gap:6px;flex-wrap:wrap">
            ${sp ? `<span class="badge warn">${esc(e.badge)}</span>`
                 : `<span class="tag out">${esc(e.sector || (e.symbol ? G.stockBy[e.symbol].name : ''))}</span>`}
            <span class="badge ${st}">${STATE_LABEL[st]}</span></div>
        </article>`;
      }).join('') : '<div class="muted" style="text-align:center;font-size:12px;padding:26px 0">아직 소식이 없습니다. 시장을 지켜보세요</div>'}
    </div>
  </div>${tabbarHTML('news')}`;
}

/* ---------- 랭킹 ---------- */

function renderRank(){
  const key = rankTab === 'return' ? 'rankReturn' : rankTab === 'risk' ? 'rankRisk' : 'rankTotal';
  const rows = G.rank.slice().sort((a,b) => a[key]-b[key]);
  app.innerHTML = `<div class="wrap">
    ${topbarHTML('랭킹')}
    <div class="seg2">${[['return','수익률'],['risk','위험조정'],['total','종합']].map(([k,l]) =>
      `<button data-rank="${k}" class="${rankTab===k?'on':''}">${l}</button>`).join('')}</div>
    ${rankTab !== 'return' ? `<div class="muted" style="font-size:11px;margin:-4px 0 10px">
      위험조정 점수는 틱별 수익률의 평균을 표준편차로 나눈 값입니다. 같은 수익이면 덜 흔들린 쪽이 높습니다</div>` : ''}
    <div class="list" style="gap:8px">
      ${rows.map(r => `<div class="rankrow ${r.me?'me':''}">
        <span class="no">${r[key]}</span>
        <span class="truncate" style="flex:1;font-size:14px;font-weight:700">${esc(r.name)}</span>
        <span style="text-align:right">
          <span class="num" style="font-size:14px;font-weight:700;color:${r.me?'var(--bg)':(r.ret>0?'var(--up)':r.ret<0?'var(--down)':'var(--muted)')}">
            ${rankTab==='risk' ? r.risk.toFixed(3) : pct(r.ret)}</span><br>
          <span class="num" style="font-size:11px;color:${r.me?'rgba(251,247,240,.7)':'var(--muted)'}">
            ${won(r.asset)}원 · MDD ${(r.mdd*100).toFixed(1)}%</span></span>
      </div>`).join('')}
    </div>
  </div>${tabbarHTML('rank')}`;
}

/* ---------- 종목 상세 ---------- */

function renderDetail(){
  const s = detailSymbol, st = G.stockBy[s], p = G.player;
  const price = G.prices[s], diff = price - st.initial_price;
  const chg = price/st.initial_price - 1;
  const q = p.holdings[s] || 0, avg = p.avgCost[s] || 0;
  const badge = G.badges[s];
  const maxBuy = maxBuyable(G, p, s);
  const free = q - (p.reservedQty[s] || 0);
  // 최대 수량을 눌러 둔 사이에 가격이 오르면 한도가 줄어든다.
  // 버튼을 죽여 두는 대신 조용히 한도에 맞춘다. 아래 안내에 한도가 늘 떠 있다.
  if (side === 'buy' && maxBuy > 0 && qty > maxBuy) qty = maxBuy;
  if (side === 'sell' && free > 0 && qty > free) qty = free;
  const amount = qty * price, fee = Math.round(amount * G.P.fee_rate);
  const byCash = Math.floor(p.cash / (price * (1 + G.P.fee_rate)));
  const limitedBy = maxBuy < byCash ? '비중 상한' : '현금';
  const over = side === 'buy' && maxBuy <= 0;
  const needReason = side === 'buy' && !reason;
  const related = G.news.filter(e => e.symbol === s || e.sector === st.sector).slice(0,2);
  const hist = G.history[s];

  app.innerHTML = `<div class="wrap detail">
    <div class="row" style="gap:10px;padding:18px 0 12px">
      <button id="back" aria-label="뒤로 가기" style="width:34px;height:34px;border-radius:10px;
        background:var(--card);display:flex;align-items:center;justify-content:center;flex-shrink:0">${SVG.back}</button>
      <span style="font-size:18px;font-weight:700">${esc(st.name)}</span>
      <span class="tag">${esc(st.sector)}</span>
      <span class="pill num" style="margin-left:auto">${SVG.clock}${clock((G.total-G.tick)*G.sc.tick_seconds)}</span>
    </div>
    ${tickerHTML()}
    ${badge ? `<div class="banner" style="margin-bottom:10px">${SVG.alert}
      <span><span class="t">${esc(badge.label)}</span><br>
      <span class="s">공매도 집중으로 위험하지만, 일시적으로 오를 수 있습니다</span></span></div>` : ''}
    <div class="display num" style="font-size:30px;line-height:1.2">${won(price)}</div>
    <div class="num ${cls(diff)}" style="font-size:14px;font-weight:700">${signedWon(diff)} (${pct(chg)})</div>
    <div class="card" style="margin-top:12px;padding:8px"><canvas id="chart"></canvas></div>
    <div class="card" style="margin-top:10px">
      <div class="kv"><span class="k">보유 수량</span><span class="v num">${q}주</span></div>
      <div class="kv"><span class="k">평균매입가</span><span class="v num">${avg ? won(avg)+'원' : '―'}</span></div>
      <div class="kv"><span class="k">평가손익</span><span class="v num ${q?cls(price-avg):''}">
        ${q ? signedWon(q*(price-avg)) + ' (' + pct(price/avg-1) + ')' : '―'}</span></div>
    </div>
    <div class="card" style="margin-top:10px">
      <div class="kv"><span class="k">시작가</span><span class="v num">${won(st.initial_price)}원</span></div>
      <div class="kv"><span class="k">오늘 최고가</span>
        <span class="v num up">${won(Math.max(...hist))}원</span></div>
      <div class="kv"><span class="k">오늘 최저가</span>
        <span class="v num down">${won(Math.min(...hist))}원</span></div>
      <div class="kv"><span class="k">성격</span>
        <span class="v">${NARRATIVE_LABEL[st.narrative] || '―'}</span></div>
    </div>
    <div class="sechead"><span class="h">관련 뉴스</span></div>
    <div class="list">${related.length ? related.map(e => `<div class="card">
        <div class="row between" style="gap:8px;align-items:flex-start">
          <span style="font-size:13px;font-weight:700;line-height:1.4">${esc(e.headline)}</span>
          <span class="badge ${stateOf(e)}">${STATE_LABEL[stateOf(e)]}</span></div>
      </div>`).join('')
      : `<div class="card"><span class="muted" style="font-size:12px">
         이 종목과 관련된 소식이 아직 없습니다. 첫 헤드라인은 7분쯤 뒤에 뜹니다</span></div>`}</div>
  </div>
  <div class="panel"><div class="inner">
    <div class="seg">
      <button data-side="buy" class="${side==='buy'?'on-buy':''}">매수</button>
      <button data-side="sell" class="${side==='sell'?'on-sell':''}">매도</button>
    </div>
    <div class="row between" style="margin-top:12px">
      <span class="muted" style="font-size:12px">수량</span>
      <span class="row" style="gap:12px">
        <button class="step" data-q="-1" aria-label="수량 1 줄이기">${SVG.minus}</button>
        <input id="qty" class="qty" inputmode="numeric" value="${qty}" aria-label="주문 수량">
        <button class="step" data-q="1" aria-label="수량 1 늘리기">${SVG.plus}</button>
      </span>
    </div>
    <div class="qrow">
      <button class="qbtn" data-q="-100">-100</button>
      <button class="qbtn" data-q="-10">-10</button>
      <button class="qbtn" data-q="10">+10</button>
      <button class="qbtn" data-q="100">+100</button>
      <button class="qbtn strong" id="maxBtn">${side === 'buy' ? '최대' : '전량'}</button>
    </div>
    <div class="muted" style="font-size:11px;margin-top:6px">${
      side === 'buy'
        ? (maxBuy > 0 ? `최대 ${maxBuy.toLocaleString('ko-KR')}주까지 담을 수 있습니다 (${limitedBy} 기준)`
                      : '현금이나 비중 상한 때문에 더 담을 수 없습니다')
        : (free > 0 ? `보유 ${free.toLocaleString('ko-KR')}주` : '팔 수 있는 수량이 없습니다')}</div>
    <div class="row between" style="margin-top:8px;font-size:12px">
      <span class="muted">예상 금액</span>
      <span class="num" style="font-weight:700">${won(amount)}원
        <span class="muted" style="font-weight:400">(수수료 ${won(fee)}원)</span></span>
    </div>
    ${side==='buy' ? `<div style="margin-top:10px">
      <div style="font-size:11px;margin-bottom:6px;${needReason
        ? 'color:var(--ink);font-weight:700' : 'color:var(--muted)'}">
        매수 근거를 하나 고르세요${needReason ? '' : ' (선택함)'}</div>
      <div class="chips${needReason ? ' ask' : ''}${Date.now() < askUntil ? ' flash' : ''}">${REASONS.map(r =>
        `<button class="chip ${reason===r?'on':''}" data-reason="${r}">${reason===r?SVG.check:''}${r}</button>`).join('')}</div>
    </div>` : ''}
    ${over ? `<div class="up" style="font-size:12px;font-weight:700;margin-top:10px">
      현금이나 비중 상한 때문에 지금은 더 담을 수 없습니다</div>` : ''}
    <button id="order" class="cta ${side==='sell'?'sell':''} ${needReason?'ghost':''}"
      ${(over || qty<1 || G.ended) ? 'disabled' : ''}>
      ${needReason ? '매수 근거를 고르세요'
                   : `${qty}주 ${side==='buy'?'매수':'매도'} 주문하기`}</button>
  </div></div>`;
  drawChart(G.history[s], chg >= 0);
}

function drawChart(data, rising){
  const cv = document.getElementById('chart');
  if (!cv) return;
  const dpr = window.devicePixelRatio || 1;
  // 화면이 길수록 차트를 키운다. 고정 높이로 두면 긴 폰에서 위가 텅 빈다.
  const h = Math.round(Math.max(170, Math.min(300, window.innerHeight * 0.26)));
  cv.style.height = h + 'px';
  const w = cv.clientWidth;
  cv.width = w * dpr; cv.height = h * dpr;
  const ctx = cv.getContext('2d');
  ctx.scale(dpr, dpr);
  ctx.clearRect(0, 0, w, h);
  const pts = data.slice(-90);
  if (pts.length < 2) return;
  const min = Math.min(...pts), max = Math.max(...pts);
  const pad = (max - min) * 0.12 || 1;
  const lo = min - pad, hi = max + pad;
  const padR = 52, padY = 10;
  const X = i => (i / (pts.length - 1)) * (w - padR - 8) + 4;
  const Y = v => padY + (1 - (v - lo) / (hi - lo)) * (h - padY * 2);
  const color = rising ? '#C13B3B' : '#3457B2';

  ctx.strokeStyle = 'rgba(33,26,46,0.08)';
  ctx.lineWidth = 1;
  ctx.font = '10px "Noto Sans KR", sans-serif';
  ctx.fillStyle = '#5C5548';
  ctx.textAlign = 'left';
  for (let k = 0; k <= 3; k++){
    const v = lo + (hi - lo) * (k / 3), y = Y(v);
    ctx.beginPath(); ctx.moveTo(4, y); ctx.lineTo(w - padR, y); ctx.stroke();
    ctx.fillText(Math.round(v).toLocaleString('ko-KR'), w - padR + 6, y + 3);
  }
  const grad = ctx.createLinearGradient(0, padY, 0, h - padY);
  grad.addColorStop(0, color + '38'); grad.addColorStop(1, color + '00');
  ctx.beginPath();
  ctx.moveTo(X(0), Y(pts[0]));
  pts.forEach((v, i) => ctx.lineTo(X(i), Y(v)));
  ctx.lineTo(X(pts.length - 1), h - padY); ctx.lineTo(X(0), h - padY); ctx.closePath();
  ctx.fillStyle = grad; ctx.fill();
  ctx.beginPath();
  pts.forEach((v, i) => i ? ctx.lineTo(X(i), Y(v)) : ctx.moveTo(X(i), Y(v)));
  ctx.strokeStyle = color; ctx.lineWidth = 2.5;
  ctx.lineJoin = 'round'; ctx.lineCap = 'round'; ctx.stroke();
  ctx.beginPath();
  ctx.arc(X(pts.length - 1), Y(pts[pts.length - 1]), 3.5, 0, Math.PI * 2);
  ctx.fillStyle = color; ctx.fill();
}
