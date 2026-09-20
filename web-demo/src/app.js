/* ---------- 라우팅과 입력 ---------- */

function render(){
  if (screen === 'start') return renderStart();
  if (screen === 'home') return renderHome();
  if (screen === 'market') return renderMarket();
  if (screen === 'news') return renderNews();
  if (screen === 'rank') return renderRank();
  if (screen === 'detail') return renderDetail();
  if (screen === 'result') return renderResult();
}

/* 화면을 옮길 때 브라우저 기록을 쌓는다.
   이게 없으면 휴대폰 뒤로가기가 앱을 통째로 닫아 버린다. */
function navigate(next, sym){
  if (sym) detailSymbol = sym;
  screen = next;
  try {
    history.pushState({ screen: next, detailSymbol }, '');
  } catch (e) { /* 기록을 못 쌓아도 화면 이동은 되어야 한다 */ }
  render();
}

function goDetail(sym){
  side = 'buy'; qty = 1; reason = null;
  navigate('detail', sym);
}

window.addEventListener('popstate', (ev) => {
  const st = ev.state;
  if (!st || !st.screen) return;
  screen = st.screen;
  if (st.detailSymbol) detailSymbol = st.detailSymbol;
  if (screen === 'detail'){ side = 'buy'; qty = 1; reason = null; }
  render();
});

function togglePause(){
  if (G.ended) return;
  paused = !paused;
  if (paused) clearInterval(timer); else startTimer();
  render();
}

app.addEventListener('click', (ev) => {
  const t = ev.target.closest(
    '[data-tab],[data-stock],[data-go],[data-rank],[data-sort],[data-side],[data-q],' +
    '[data-reason],#back,#order,#again,#maxBtn,#pause');
  if (!t) return;

  if (t.dataset.tab) return navigate(t.dataset.tab);
  if (t.dataset.go) return goDetail(t.dataset.go);
  if (t.dataset.stock) return goDetail(t.dataset.stock);
  if (t.dataset.rank){ rankTab = t.dataset.rank; return render(); }
  if (t.dataset.sort){ sortBy = t.dataset.sort; return render(); }
  if (t.id === 'back'){ history.back(); return; }
  if (t.id === 'pause') return togglePause();
  if (t.id === 'again'){ clearInterval(timer); paused = false; return navigate('start'); }

  if (t.dataset.side){ side = t.dataset.side; qty = 1; reason = null; return render(); }
  if (t.dataset.q){
    const step = Number(t.dataset.q);
    const cap = side === 'buy'
      ? maxBuyable(G, G.player, detailSymbol)
      : (G.player.holdings[detailSymbol] || 0) - (G.player.reservedQty[detailSymbol] || 0);
    qty = Math.max(1, Math.min(qty + step, Math.max(1, cap)));
    return render();
  }
  if (t.dataset.reason){ reason = t.dataset.reason; return render(); }

  if (t.id === 'maxBtn'){
    const m = side === 'buy'
      ? maxBuyable(G, G.player, detailSymbol)
      : (G.player.holdings[detailSymbol] || 0) - (G.player.reservedQty[detailSymbol] || 0);
    if (m > 0){ qty = m; render(); }
    return;
  }
  if (t.id === 'order'){
    // 근거를 안 고른 채로 눌렀을 때 조용히 아무 일도 없으면 고장으로 보인다.
    // 무엇이 빠졌는지 말해 주고 근거 줄을 잠깐 강조한다.
    if (side === 'buy' && !reason){
      toast('매수 근거를 하나 골라야 주문할 수 있습니다');
      askUntil = Date.now() + 900;
      setTimeout(() => { if (screen === 'detail') render(); }, 950);
      return render();
    }
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

try { history.replaceState({ screen: 'start' }, ''); } catch (e) { /* 무시 */ }
render();
