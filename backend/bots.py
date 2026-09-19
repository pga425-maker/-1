"""봇 5종 — 파라미터 튜닝(8번)과 자가 검증(17번)이 함께 쓴다.

리허설 하네스(9번)의 봇은 여기서 나온 전략에 군집 행동(herding)과 반응 지연을
얹어 실제 서버에 HTTP/WebSocket 으로 붙는다. 이 모듈의 봇은 서버를 타지 않고
계산만 한다.

모든 봇은 한 틱에 최대 하나의 주문만 낸다. 실제 참가자도 휴대폰으로 한 번에
한 주문씩 넣는다.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

# 전략 5종. 스펙 8번.
STRATEGIES = ("momentum", "contrarian", "random", "buy_and_hold", "news_reactive")

# 매수 근거 태그. 분석(12번)에서 근거별 성적표를 만들 때 쓴다.
REASON_BY_STRATEGY = {
    "momentum": "차트추세",
    "contrarian": "저평가",
    "random": "직감",
    "buy_and_hold": "분산",
    "news_reactive": "뉴스",
}


@dataclass
class Order:
    bot_id: int
    symbol: str
    side: str          # buy | sell
    qty: int
    reason: str | None
    submit_tick: int
    quoted_price: int = 0   # 접수 시점에 봇이 본 가격. 참가자의 '예상 금액'과 같다
    reserved: int = 0       # 접수 때 잡아 둔 현금. 체결 시 정확히 되돌린다


@dataclass
class MarketView:
    """봇이 볼 수 있는 것만 담는다. 시나리오 원본은 보이지 않는다."""

    tick: int
    prices: dict[str, int]
    history: dict[str, list[int]]
    symbols: tuple[str, ...]
    fresh_headline_symbols: tuple[str, ...]   # 최근 헤드라인이 가리킨 종목
    ticks_since_headline: int


@dataclass
class Bot:
    bot_id: int
    strategy: str
    cash: int
    starting_cash: int
    rng: random.Random
    holdings: dict[str, int] = field(default_factory=dict)
    avg_cost: dict[str, float] = field(default_factory=dict)
    reserved_cash: int = 0
    reserved_qty: dict[str, int] = field(default_factory=dict)
    seeded: bool = False

    # ---------- 자산 ----------

    def total_asset(self, prices: dict[str, int]) -> int:
        value = sum(qty * prices[sym] for sym, qty in self.holdings.items())
        return self.cash + self.reserved_cash + value

    def available_cash(self) -> int:
        return self.cash

    def free_qty(self, symbol: str) -> int:
        return self.holdings.get(symbol, 0) - self.reserved_qty.get(symbol, 0)

    # ---------- 주문 ----------

    def decide(self, view: MarketView, max_position_ratio: float) -> Order | None:
        handler = getattr(self, f"_decide_{self.strategy}")
        return handler(view, max_position_ratio)

    def _affordable_qty(
        self, symbol: str, view: MarketView, ratio_of_cash: float,
        max_position_ratio: float,
    ) -> int:
        price = view.prices[symbol]
        budget = int(self.available_cash() * ratio_of_cash)
        # 한 종목 총자산 50% 초과 보유 금지. 주문 시점 기준으로 본다(결정-04).
        total = self.total_asset(view.prices)
        held_value = self.holdings.get(symbol, 0) * price
        room = int(total * max_position_ratio) - held_value
        budget = min(budget, max(0, room))
        return max(0, budget // price)

    def _buy(self, symbol, view, ratio, max_position_ratio) -> Order | None:
        qty = self._affordable_qty(symbol, view, ratio, max_position_ratio)
        if qty <= 0:
            return None
        return Order(self.bot_id, symbol, "buy", qty,
                     REASON_BY_STRATEGY[self.strategy], view.tick)

    def _sell(self, symbol, view, ratio) -> Order | None:
        held = self.free_qty(symbol)
        qty = int(held * ratio)
        if qty <= 0:
            return None
        return Order(self.bot_id, symbol, "sell", qty, None, view.tick)

    # ---------- 전략 ----------

    @staticmethod
    def _window_return(view: MarketView, symbol: str, window: int = 5) -> float:
        hist = view.history[symbol]
        if len(hist) <= window:
            return 0.0
        past = hist[-window - 1]
        return (hist[-1] - past) / past if past else 0.0

    def _decide_momentum(self, view, max_position_ratio):
        """직전 5틱 상승률 상위를 산다. 떨어지는 보유분은 정리한다."""
        if self.rng.random() < 0.06:
            best = max(view.symbols, key=lambda s: self._window_return(view, s))
            if self._window_return(view, best) > 0:
                return self._buy(best, view, 0.35, max_position_ratio)
        if self.rng.random() < 0.04 and self.holdings:
            held = [s for s in self.holdings if self.free_qty(s) > 0]
            if held:
                worst = min(held, key=lambda s: self._window_return(view, s))
                if self._window_return(view, worst) < -0.01:
                    return self._sell(worst, view, 0.5)
        return None

    def _decide_contrarian(self, view, max_position_ratio):
        """하락폭이 큰 것을 산다. 많이 오른 보유분은 덜어낸다."""
        if self.rng.random() < 0.06:
            worst = min(view.symbols, key=lambda s: self._window_return(view, s))
            if self._window_return(view, worst) < 0:
                return self._buy(worst, view, 0.35, max_position_ratio)
        if self.rng.random() < 0.04 and self.holdings:
            held = [s for s in self.holdings if self.free_qty(s) > 0]
            if held:
                best = max(held, key=lambda s: self._window_return(view, s))
                if self._window_return(view, best) > 0.02:
                    return self._sell(best, view, 0.5)
        return None

    def _decide_random(self, view, max_position_ratio):
        if self.rng.random() >= 0.08:
            return None
        symbol = self.rng.choice(view.symbols)
        if self.rng.random() < 0.6:
            return self._buy(symbol, view, 0.25, max_position_ratio)
        return self._sell(symbol, view, 0.5)

    def _decide_buy_and_hold(self, view, max_position_ratio):
        """초반에 나눠 담고 그대로 둔다. 안전자산의 가치를 보여주는 대조군이다."""
        if self.seeded:
            return None
        if view.tick < 2 or view.tick > 12:
            return None
        picks = [s for s in view.symbols if self.holdings.get(s, 0) == 0]
        if not picks:
            self.seeded = True
            return None
        symbol = self.rng.choice(picks)
        order = self._buy(symbol, view, 0.34, max_position_ratio)
        if len(self.holdings) >= 2 and view.tick >= 8:
            self.seeded = True
        return order

    def _decide_news_reactive(self, view, max_position_ratio):
        """뉴스가 뜬 직후에만 움직인다. 방향은 모르고 태그만 보고 달려든다."""
        if not view.fresh_headline_symbols or view.ticks_since_headline > 4:
            return None
        if self.rng.random() >= 0.35:
            return None
        symbol = self.rng.choice(view.fresh_headline_symbols)
        return self._buy(symbol, view, 0.4, max_position_ratio)


def build_bots(
    count: int, starting_cash: int, seed: int
) -> list[Bot]:
    """전략 5종을 고르게 섞어 봇을 만든다. 시드가 같으면 같은 봇 집단이 나온다."""
    rng = random.Random(seed)
    bots: list[Bot] = []
    for i in range(count):
        strategy = STRATEGIES[i % len(STRATEGIES)]
        bots.append(Bot(
            bot_id=i, strategy=strategy, cash=starting_cash,
            starting_cash=starting_cash,
            rng=random.Random(rng.randrange(1 << 30)),
        ))
    return bots
