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

function goDetail(sym){
  detailSymbol = sym; screen = 'detail';
  side = 'buy'; qty = 1; reason = null;
  render();
}

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

  if (t.dataset.tab){ screen = t.dataset.tab; return render(); }
  if (t.dataset.go) return goDetail(t.dataset.go);
  if (t.dataset.stock) return goDetail(t.dataset.stock);
  if (t.dataset.rank){ rankTab = t.dataset.rank; return render(); }
  if (t.dataset.sort){ sortBy = t.dataset.sort; return render(); }
  if (t.id === 'back'){ screen = 'home'; return render(); }
  if (t.id === 'pause') return togglePause();
  if (t.id === 'again'){ clearInterval(timer); paused = false; screen = 'start'; return render(); }

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
