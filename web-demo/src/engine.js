/* ===================== 엔진 =====================
   서버 버전(backend/price_engine.py, index_engine.py, event_scheduler.py)의
   계산을 그대로 옮긴 것이다. 시나리오도 같은 파일을 쓴다. */

function mulberry32(seed){
  let a = seed >>> 0;
  return function(){
    a |= 0; a = (a + 0x6D2B79F5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}
function gaussFactory(rnd){
  let spare = null;
  return function(sigma){
    if (spare !== null){ const v = spare; spare = null; return v * sigma; }
    let u, v, s;
    do { u = rnd() * 2 - 1; v = rnd() * 2 - 1; s = u * u + v * v; } while (s >= 1 || s === 0);
    const m = Math.sqrt(-2 * Math.log(s) / s);
    spare = v * m;
    return u * m * sigma;
  };
}

/* 목표값 램프 보간. 구간이 바뀌는 지점에서 계단 점프가 생기지 않는다. */
function buildTargets(segments, total, initial){
  const out = new Array(total + 1).fill(initial);
  let anchor = initial;
  for (const seg of segments){
    const span = seg.to - seg.from;
    for (let t = seg.from + 1; t <= Math.min(seg.to, total); t++){
      out[t] = anchor + (seg.target - anchor) * ((t - seg.from) / span);
    }
    anchor = seg.target;
  }
  return out;
}
function buildDrift(stock, total){
  const a = new Array(total + 1).fill(0);
  for (const s of (stock.drift_segments || [])){
    for (let t = s.from + 1; t <= s.to; t++) a[t] = s.drift;
  }
  return a;
}
function decayWeights(n){
  const tau = Math.max(1, n / 2); const raw = []; let sum = 0;
  for (let k = 0; k < n; k++){ const w = Math.exp(-k / tau); raw.push(w); sum += w; }
  return raw.map(w => w / sum);
}
function rampWeights(n){
  const raw = []; let sum = 0;
  for (let k = 0; k < n; k++){ raw.push(k + 1); sum += k + 1; }
  return raw.map(w => w / sum);
}

/* 이벤트를 틱별 delta 로 미리 전개한다. 펌프 총합 < 덤프 총합과
   복리 순효과 손실을 여기서 강제한다. */
function buildEvents(sc, rnd){
  const P = sc.params;
  const bySector = {};
  for (const s of sc.stocks){ (bySector[s.sector] = bySector[s.sector] || []).push(s.symbol); }
  const byTick = new Map();
  const events = [];
  const add = (tick, symbol, d) => {
    if (!byTick.has(tick)) byTick.set(tick, {});
    const b = byTick.get(tick);
    b[symbol] = (b[symbol] || 0) + d;
  };

  for (const e of sc.events){
    const delay = e.impact_delay_ticks || 0;
    if (e.type === 'short_pressure'){
      let pump = e.pump.min + rnd() * (e.pump.max - e.pump.min);
      pump = Math.max(pump, P.min_visible_pump - 1);
      let dump = e.dump.min + rnd() * (e.dump.max - e.dump.min);
      dump = Math.max(dump, pump * (1 + P.net_loss_margin));
      const pw = rampWeights(e.pump.ticks);
      let dw = decayWeights(e.dump.ticks);
      let pd = pw.map(w => pump * w);
      let dd = dw.map(w => -dump * w);
      for (let i = 0; i < 200; i++){
        let m = 1;
        for (const d of pd.concat(dd)) m *= 1 + d;
        if (m < 1) break;
        dump *= 1.02;
        dd = dw.map(w => -dump * w);
      }
      const start = e.tick + delay;
      const deltas = pd.concat(dd);
      deltas.forEach((d, i) => add(start + i, e.symbol, d));
      events.push({
        key: e.key, type: 'short_pressure', headline: e.headline,
        body: '공매도 집중으로 위험하지만, 일시적으로 오를 수 있습니다',
        badge: '공매도 집중 과열 · 급등 후 급락 위험',
        tick: e.tick, symbol: e.symbol, sector: null, difficulty: null,
        start, end: start + deltas.length - 1,
        pumpTicks: e.pump.ticks, pumpTotal: pump, dumpTotal: dump,
        pumpStart: start, pumpEnd: start + e.pump.ticks - 1,
      });
    } else {
      const spread = e.spread_ticks || 6;
      const targets = e.symbol ? [e.symbol] : (bySector[e.sector] || []);
      const w = decayWeights(spread);
      const start = e.tick + delay;
      for (let i = 0; i < spread; i++){
        for (const sym of targets) add(start + i, sym, e.delta * w[i]);
      }
      events.push({
        key: e.key, type: 'normal', headline: e.headline || '', body: e.body || '',
        tick: e.tick, symbol: e.symbol || null, sector: e.sector || null,
        difficulty: e.difficulty, start, end: start + spread - 1,
      });
    }
  }
  events.sort((a, b) => a.tick - b.tick);
  return { byTick, events };
}

/* 틱당 가격. 방향은 절대 뒤집히지 않고, 변동폭만 깎인다. */
function computeTick(inp){
  if (!inp.liquidity) throw new Error('유동성이 0 이면 계산할 수 없다');
  const base = inp.drift + inp.noise;
  const fear = -inp.beta * inp.fearSens * (inp.fearChange / 100);
  const fx = inp.fxExposure * inp.fxSens * inp.fxChange;
  const total = base + inp.eventDelta + fear + fx;
  let f = (inp.buyQty - inp.sellQty) / inp.liquidity;
  f = Math.max(-1, Math.min(1, f));
  const damp = Math.min(inp.resistance * Math.abs(f), inp.cap);
  const actual = total * (1 - damp);
  const price = Math.max(Math.round(inp.prev * (1 + actual)), inp.minPrice);
  return { base, fear, fx, total, f, damp, actual, price };
}

/* ===================== 게임 ===================== */

const REASONS = ['실적성장','저평가','차트추세','분산','뉴스','직감'];
const BOT_KINDS = ['momentum','contrarian','random','buy_and_hold','news_reactive'];
const BOT_REASON = {momentum:'차트추세',contrarian:'저평가',random:'직감',
                    buy_and_hold:'분산',news_reactive:'뉴스'};
const BOT_NAMES = ['별하','도현','서윤','지후','하람','예린','太민','유나','건우','채원',
                   '시온','나윤','주안','다온','은결','해찬','린아','소율'];
const BEHAVIOR_LABEL = {momentum:'모멘텀추종',contrarian:'역추세',news:'뉴스반응',
                        hold:'매수후보유',mixed:'혼합'};

function makeAccount(cash){
  return { cash, reserved: 0, holdings: {}, avgCost: {}, reservedQty: {},
           buys: [], trades: [] };
}
function assetOf(acc, prices){
  let v = acc.cash + acc.reserved;
  for (const s in acc.holdings) v += acc.holdings[s] * prices[s];
  return v;
}

function createGame(sc, seed, nickname){
  const rnd = mulberry32(seed);
  const gauss = gaussFactory(rnd);
  const total = sc.total_ticks;
  const P = sc.params;

  const symbols = sc.stocks.map(s => s.symbol);
  const stockBy = {}; sc.stocks.forEach(s => stockBy[s.symbol] = s);
  const prices = {}; const history = {};
  sc.stocks.forEach(s => { prices[s.symbol] = s.initial_price; history[s.symbol] = [s.initial_price]; });

  const drift = {}; sc.stocks.forEach(s => drift[s.symbol] = buildDrift(s, total));
  const fearTargets = buildTargets(sc.fear_index.segments, total, sc.fear_index.initial);
  const fxTargets = buildTargets(sc.fx_rate.segments, total, sc.fx_rate.initial);
  const { byTick, events } = buildEvents(sc, mulberry32(seed));

  const player = makeAccount(sc.starting_cash);
  player.name = nickname;
  const bots = [];
  const botRnd = mulberry32(seed + 7);
  for (let i = 0; i < 15; i++){
    const b = makeAccount(sc.starting_cash);
    b.name = BOT_NAMES[i % BOT_NAMES.length];
    b.kind = BOT_KINDS[i % BOT_KINDS.length];
    b.rnd = mulberry32(Math.floor(botRnd() * 1e9));
    b.seeded = false;
    bots.push(b);
  }

  return {
    sc, seed, total, P, symbols, stockBy, prices, history, drift,
    fearTargets, fxTargets, byTick, events,
    tick: 0, fear: [sc.fear_index.initial], fx: [sc.fx_rate.initial],
    gauss, rnd, player, bots, queued: [], pending: [],
    news: [], badges: {}, rank: [], flow: {},
    disp: { fear: 0, fx: 0 },
    stats: { prev: sc.starting_cash, peak: sc.starting_cash, n: 0, mean: 0, m2: 0, mdd: 0 },
    contrib: { base: 0, event: 0, fear: 0, fx: 0 },
    lots: {}, buys: [], sells: [], orderLog: [],
    windowBuys: [], crowd: [],
    ended: false,
  };
}

function pushStat(st, asset){
  if (st.prev > 0){
    const r = asset / st.prev - 1;
    st.n += 1;
    const d = r - st.mean;
    st.mean += d / st.n;
    st.m2 += d * (r - st.mean);
  }
  st.prev = asset;
  if (asset > st.peak) st.peak = asset;
  else if (st.peak > 0) st.mdd = Math.max(st.mdd, 1 - asset / st.peak);
}
function riskScore(st){
  if (st.n < 2) return 0;
  const sd = Math.sqrt(st.m2 / st.n);
  return sd === 0 ? 0 : st.mean / sd;
}

function maxBuyable(G, acc, symbol){
  const p = G.prices[symbol];
  const byCash = Math.floor(acc.cash / (p * (1 + G.P.fee_rate)));
  const held = (acc.holdings[symbol] || 0) * p;
  const room = Math.floor(assetOf(acc, G.prices) * G.P.max_position_ratio) - held;
  return Math.max(0, Math.min(byCash, Math.max(0, Math.floor(room / p))));
}

function submit(G, acc, symbol, side, qty, reason){
  if (G.ended) return { ok: false, msg: '이미 끝난 판입니다' };
  if (G.tick >= G.total) return { ok: false, msg: '마지막 틱이라 체결될 수 없습니다' };
  qty = Math.floor(qty);
  if (!(qty > 0)) return { ok: false, msg: '수량은 1 이상이어야 합니다' };
  const p = G.prices[symbol];
  if (side === 'buy'){
    if (!reason) return { ok: false, msg: '매수 근거를 하나 골라야 합니다' };
    const need = Math.ceil(p * qty * (1 + G.P.fee_rate));
    if (need > acc.cash) return { ok: false, msg: '현금이 부족합니다' };
    const held = (acc.holdings[symbol] || 0) * p;
    const room = Math.floor(assetOf(acc, G.prices) * G.P.max_position_ratio) - held;
    if (p * qty > Math.max(0, room)) return { ok: false, msg: '한 종목에 총자산의 50% 까지만 담을 수 있습니다' };
    acc.cash -= need; acc.reserved += need;
    G.queued.push({ acc, symbol, side, qty, reason, reserved: need, tick: G.tick });
  } else {
    const free = (acc.holdings[symbol] || 0) - (acc.reservedQty[symbol] || 0);
    if (qty > free) return { ok: false, msg: '보유 수량이 부족합니다' };
    acc.reservedQty[symbol] = (acc.reservedQty[symbol] || 0) + qty;
    G.queued.push({ acc, symbol, side, qty, reason: null, reserved: 0, tick: G.tick });
  }
  return { ok: true, msg: `${qty.toLocaleString('ko-KR')}주 주문 접수. 다음 틱 가격으로 체결됩니다` };
}

function fill(G, o){
  const acc = o.acc, p = G.prices[o.symbol], fee = G.P.fee_rate;
  if (o.side === 'buy'){
    acc.reserved -= o.reserved; acc.cash += o.reserved;
    const byCash = Math.floor(acc.cash / (p * (1 + fee)));
    const held = (acc.holdings[o.symbol] || 0) * p;
    const room = Math.floor(assetOf(acc, G.prices) * G.P.max_position_ratio) - held;
    const byRoom = Math.max(0, Math.floor(room / p));
    const qty = Math.min(o.qty, byCash, byRoom);
    if (qty <= 0) return 0;
    const gross = p * qty, f = Math.round(gross * fee);
    acc.cash -= gross + f;
    const prevQ = acc.holdings[o.symbol] || 0;
    const prevC = (acc.avgCost[o.symbol] || 0) * prevQ;
    acc.holdings[o.symbol] = prevQ + qty;
    acc.avgCost[o.symbol] = (prevC + gross + f) / (prevQ + qty);
    acc.buys.push({ symbol: o.symbol, tick: o.tick, reason: o.reason, qty, price: p });
    acc.trades.push({ side: 'buy', symbol: o.symbol, qty, price: p, tick: G.tick, reason: o.reason });
    if (acc === G.player){
      (G.lots[o.symbol] = G.lots[o.symbol] || []).push({ qty, price: p, reason: o.reason });
      G.buys = acc.buys; G.orderLog = acc.trades;
    }
    // 군중이 무엇을 사고 있는지 집계한다
    G.windowBuys.push({ acc, symbol: o.symbol, value: qty * p });
    return qty;
  }
  acc.reservedQty[o.symbol] = Math.max(0, (acc.reservedQty[o.symbol] || 0) - o.qty);
  const qty = Math.min(o.qty, acc.holdings[o.symbol] || 0);
  if (qty <= 0) return 0;
  const gross = p * qty, f = Math.round(gross * fee);
  acc.cash += gross - f;
  acc.holdings[o.symbol] -= qty;
  if (acc.holdings[o.symbol] === 0){ delete acc.holdings[o.symbol]; delete acc.avgCost[o.symbol]; }
  acc.trades.push({ side: 'sell', symbol: o.symbol, qty, price: p, tick: G.tick });
  if (acc === G.player){
    let rest = qty;
    const q = G.lots[o.symbol] || [];
    while (rest > 0 && q.length){
      const lot = q[0];
      const take = Math.min(rest, lot.qty);
      G.sells.push({ reason: lot.reason, qty: take, buy: lot.price, exit: p });
      lot.qty -= take; rest -= take;
      if (lot.qty === 0) q.shift();
    }
    G.orderLog = acc.trades;
  }
  return qty;
}

function windowReturn(G, symbol, w){
  const h = G.history[symbol];
  if (h.length <= w) return 0;
  const past = h[h.length - 1 - w];
  return past ? h[h.length - 1] / past - 1 : 0;
}

function botAct(G, b){
  const r = b.rnd;
  const syms = G.symbols;
  const pick = (s, ratio) => {
    const p = G.prices[s];
    const qty = Math.min(Math.floor(b.cash * ratio / p), maxBuyable(G, b, s));
    if (qty > 0) submit(G, b, s, 'buy', qty, BOT_REASON[b.kind]);
  };
  const dump = (s, ratio) => {
    const free = (b.holdings[s] || 0) - (b.reservedQty[s] || 0);
    const qty = Math.floor(free * ratio);
    if (qty > 0) submit(G, b, s, 'sell', qty, null);
  };
  if (b.kind === 'momentum'){
    if (r() < 0.07){
      const best = syms.reduce((a, c) => windowReturn(G, c, 5) > windowReturn(G, a, 5) ? c : a, syms[0]);
      if (windowReturn(G, best, 5) > 0) pick(best, 0.32);
    }
    if (r() < 0.05){
      const held = Object.keys(b.holdings);
      if (held.length){
        const worst = held.reduce((a, c) => windowReturn(G, c, 5) < windowReturn(G, a, 5) ? c : a, held[0]);
        if (windowReturn(G, worst, 5) < -0.01) dump(worst, 0.5);
      }
    }
  } else if (b.kind === 'contrarian'){
    if (r() < 0.07){
      const worst = syms.reduce((a, c) => windowReturn(G, c, 5) < windowReturn(G, a, 5) ? c : a, syms[0]);
      if (windowReturn(G, worst, 5) < 0) pick(worst, 0.32);
    }
    if (r() < 0.05){
      const held = Object.keys(b.holdings);
      if (held.length){
        const best = held.reduce((a, c) => windowReturn(G, c, 5) > windowReturn(G, a, 5) ? c : a, held[0]);
        if (windowReturn(G, best, 5) > 0.02) dump(best, 0.5);
      }
    }
  } else if (b.kind === 'random'){
    if (r() < 0.09){
      const s = syms[Math.floor(r() * syms.length)];
      if (r() < 0.6) pick(s, 0.22); else dump(s, 0.5);
    }
  } else if (b.kind === 'buy_and_hold'){
    if (!b.seeded && G.tick >= 2 && G.tick <= 12 && r() < 0.6){
      const pool = syms.filter(s => !b.holdings[s]);
      if (pool.length) pick(pool[Math.floor(r() * pool.length)], 0.33);
      if (Object.keys(b.holdings).length >= 3) b.seeded = true;
    }
  } else if (b.kind === 'news_reactive'){
    const fresh = G.news[0];
    if (fresh && G.tick - fresh.tick <= 4 && r() < 0.38){
      const targets = fresh.symbol ? [fresh.symbol]
        : G.sc.stocks.filter(s => s.sector === fresh.sector).map(s => s.symbol);
      if (targets.length) pick(targets[Math.floor(r() * targets.length)], 0.35);
    }
  }
  // 경고 배지를 보고도 미끼를 무는 사람은 늘 있다
  const badged = Object.keys(G.badges);
  if (badged.length && r() < 0.1) pick(badged[Math.floor(r() * badged.length)], 0.25);
}

function step(G){
  if (G.ended || G.tick >= G.total) return;
  const sc = G.sc, P = G.P;
  G.tick += 1;
  const t = G.tick;
  G.pending = G.queued; G.queued = [];

  const fearPrev = G.fear[G.fear.length - 1];
  let fearNext = fearPrev + sc.fear_index.theta * (G.fearTargets[t] - fearPrev)
    + G.gauss(sc.fear_index.noise_sigma);
  fearNext = Math.max(0, Math.min(100, fearNext));
  G.fear.push(fearNext);
  const fxPrev = G.fx[G.fx.length - 1];
  const fxNext = fxPrev + sc.fx_rate.theta * (G.fxTargets[t] - fxPrev)
    + G.gauss(sc.fx_rate.noise_sigma);
  G.fx.push(fxNext);
  const fearChange = fearNext - fearPrev;
  const fxChange = (fxNext - fxPrev) / fxPrev;

  const buyQ = {}, sellQ = {};
  G.symbols.forEach(s => { buyQ[s] = 0; sellQ[s] = 0; });
  for (const o of G.pending){ (o.side === 'buy' ? buyQ : sellQ)[o.symbol] += o.qty; }

  const evd = G.byTick.get(t) || {};
  const results = {};
  for (const s of G.symbols){
    const st = G.stockBy[s];
    const prev = G.prices[s];
    const res = computeTick({
      prev, liquidity: st.liquidity, beta: st.beta, fxExposure: st.fx_exposure,
      drift: G.drift[s][t], noise: G.gauss(st.noise_sigma != null ? st.noise_sigma : P.noise_sigma),
      eventDelta: evd[s] || 0, fearChange, fxChange,
      buyQty: buyQ[s], sellQty: sellQ[s],
      resistance: P.resistance_coef, cap: P.dampening_cap,
      fearSens: P.fear_sensitivity, fxSens: P.fx_sensitivity, minPrice: P.min_price,
    });
    // 원인 분해: 이번 틱에 들고 있던 만큼만 기여로 잡는다
    const qty = G.player.holdings[s] || 0;
    if (qty){
      const basis = qty * prev, keep = 1 - res.damp;
      G.contrib.base  += basis * res.base * keep;
      G.contrib.event += basis * (evd[s] || 0) * keep;
      G.contrib.fear  += basis * res.fear * keep;
      G.contrib.fx    += basis * res.fx * keep;
    }
    G.prices[s] = res.price;
    G.history[s].push(res.price);
    results[s] = res;
  }

  for (const o of G.pending) fill(G, o);

  // 뉴스와 배지
  for (const e of G.events){
    if (e.tick === t && e.headline) G.news.unshift(e);
  }
  G.badges = {};
  for (const e of G.events){
    if (e.type === 'short_pressure' && t >= e.tick && t <= e.end){
      G.badges[e.symbol] = {
        label: e.badge,
        phase: t < e.start ? 'pending' : (t <= e.pumpEnd ? 'pump' : 'dump'),
      };
    }
  }

  for (const b of G.bots) botAct(G, b);
  pushStat(G.stats, assetOf(G.player, G.prices));
  for (const b of G.bots){
    b.stats = b.stats || { prev: b.cash, peak: b.cash, n: 0, mean: 0, m2: 0, mdd: 0 };
    pushStat(b.stats, assetOf(b, G.prices));
  }

  const iv = P.snapshot_interval_ticks;
  if (t % iv === 0 || t === 1){
    const anchor = Math.max(0, t - iv);
    G.disp = { fear: fearNext - G.fear[anchor], fx: fxNext - G.fx[anchor] };
    refreshRank(G);
    refreshFlow(G, buyQ, sellQ);
    detectCrowd(G, anchor, t);
  }
  if (t >= G.total){ G.ended = true; refreshRank(G); }
}

function refreshRank(G){
  const rows = [];
  const push = (acc, st, me) => {
    const a = assetOf(acc, G.prices);
    rows.push({ name: acc.name, asset: a, ret: a / G.sc.starting_cash - 1,
                risk: riskScore(st), mdd: st.mdd, me });
  };
  push(G.player, G.stats, true);
  for (const b of G.bots) push(b, b.stats || {n:0,mean:0,m2:0,mdd:0}, false);
  rows.sort((a, b) => b.ret - a.ret || a.mdd - b.mdd);
  rows.forEach((r, i) => r.rankReturn = i + 1);
  const byRisk = rows.slice().sort((a, b) => b.risk - a.risk || a.mdd - b.mdd);
  byRisk.forEach((r, i) => r.rankRisk = i + 1);
  const byTotal = rows.slice().sort(
    (a, b) => (a.rankReturn + a.rankRisk) / 2 - (b.rankReturn + b.rankRisk) / 2 || a.mdd - b.mdd);
  byTotal.forEach((r, i) => r.rankTotal = i + 1);
  G.rank = rows;
}

/* 한 종목에 매수가 몰린 구간을 찾는다. 결과 화면의 군중심리 비교에 쓴다. */
function detectCrowd(G, from, to){
  const rows = G.windowBuys;
  G.windowBuys = [];
  if (!rows.length) return;
  const byS = {}, who = {};
  let grand = 0;
  for (const r of rows){
    byS[r.symbol] = (byS[r.symbol] || 0) + r.value;
    (who[r.symbol] = who[r.symbol] || new Set()).add(r.acc);
    grand += r.value;
  }
  const top = Object.keys(byS).reduce((a, c) => byS[c] > byS[a] ? c : a, Object.keys(byS)[0]);
  if (!grand || byS[top] / grand < 0.6) return;
  if (who[top].size < 3) return;
  G.crowd.push({ from, to, symbol: top, share: byS[top] / grand, followers: who[top] });
}

function refreshFlow(G, buyQ, sellQ){
  const flow = {};
  for (const s of G.symbols){
    flow[s] = { net: (buyQ[s] || 0) - (sellQ[s] || 0) };
  }
  G.flow = flow;
}
