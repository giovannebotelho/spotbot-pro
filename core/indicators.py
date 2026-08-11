import math
import numpy as np
import pandas as pd
from services.binance_client import extract_closes, extract_volumes

def calculate_trade_result(price_bought, executed_qty, price_sold):
    return (price_sold - price_bought) * executed_qty

async def calculate_fee(client, symbol, executed_qty, price_sold):
    try:
        order_val = executed_qty * price_sold
        return order_val * 0.00075
    except Exception:
        return (executed_qty * price_sold) * 0.001

def calculate_rsi(closes, period=14):
    if len(closes) < period + 1:
        return 50.0
    series = pd.Series(closes)
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    
    last_gain = gain.iloc[-1]
    last_loss = loss.iloc[-1]
    
    if last_loss == 0:
        return 100.0
    rs = last_gain / last_loss
    return float(100.0 - (100.0 / (1.0 + rs)))

def calculate_macd(closes, fast=12, slow=26, signal=9):
    if len(closes) < slow + signal:
        return 0.0, 0.0
    series = pd.Series(closes)
    exp1 = series.ewm(span=fast, adjust=False).mean()
    exp2 = series.ewm(span=slow, adjust=False).mean()
    macd = exp1 - exp2
    signal_line = macd.ewm(span=signal, adjust=False).mean()
    return float(macd.iloc[-1]), float(signal_line.iloc[-1])

def calculate_bollinger_bands(closes, period=20, std_dev=2):
    if len(closes) < period:
        c = closes[-1] if closes else 0.0
        return c, c, c
    series = pd.Series(closes)
    ma = series.rolling(window=period).mean()
    std = series.rolling(window=period).std()
    upper = ma + (std * std_dev)
    lower = ma - (std * std_dev)
    return float(lower.iloc[-1]), float(ma.iloc[-1]), float(upper.iloc[-1])

def calculate_vwap(closes, volumes):
    if not closes or not volumes or len(closes) != len(volumes):
        return closes[-1] if closes else 0.0
    df = pd.DataFrame({'close': closes, 'volume': volumes})
    df['tp'] = df['close']
    df['pv'] = df['tp'] * df['volume']
    cum_pv = df['pv'].sum()
    cum_vol = df['volume'].sum()
    if cum_vol == 0:
        return closes[-1]
    return float(cum_pv / cum_vol)

def calculate_ema(closes, period):
    if len(closes) < period:
        return closes[-1] if closes else 0.0
    series = pd.Series(closes)
    return float(series.ewm(span=period, adjust=False).mean().iloc[-1])

def calculate_adx(klines, period=14):
    if len(klines) < period * 2:
        return 25.0
    try:
        highs = pd.Series([float(k[2]) for k in klines])
        lows = pd.Series([float(k[3]) for k in klines])
        closes = pd.Series([float(k[4]) for k in klines])

        up_move = highs.diff()
        down_move = -lows.diff()

        plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
        minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

        tr1 = highs - lows
        tr2 = (highs - closes.shift()).abs()
        tr3 = (lows - closes.shift()).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

        atr = tr.rolling(window=period).mean()
        plus_di = 100 * (pd.Series(plus_dm).rolling(window=period).mean() / atr)
        minus_di = 100 * (pd.Series(minus_dm).rolling(window=period).mean() / atr)

        dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di)
        adx = dx.rolling(window=period).mean().iloc[-1]
        return float(adx) if not np.isnan(adx) else 25.0
    except Exception:
        return 25.0

def calculate_hurst_exponent(closes, max_lag=20):
    if len(closes) < 30:
        return 0.50
    closes_arr = np.array(closes)
    lags = range(2, min(max_lag, len(closes) // 2))
    tau = [np.sqrt(np.std(np.subtract(closes_arr[lag:], closes_arr[:-lag]))) for lag in lags]
    
    if len(tau) < 2 or np.all(tau[0] == tau):
        return 0.50

    poly = np.polyfit(np.log(lags), np.log(tau), 1)
    hurst = poly[0] * 2.0
    return float(np.clip(hurst, 0.0, 1.0))

def detect_market_regime(klines):
    closes = extract_closes(klines)
    if len(closes) < 50:
        return "REGIME_RANGE_BOUND", 0.45

    first_price = closes[-24] if len(closes) >= 24 else closes[0]
    price_change_24h = ((closes[-1] - first_price) / first_price) * 100
    
    if price_change_24h < -3.5:
        return "REGIME_CRASH_PANIC", 0.30

    hurst = calculate_hurst_exponent(closes)
    ema50 = calculate_ema(closes, 50)
    ema200 = calculate_ema(closes, 200)

    if hurst > 0.55 and closes[-1] > ema50 and ema50 > ema200:
        return "REGIME_BULL_TREND", hurst
    elif hurst < 0.48:
        return "REGIME_RANGE_BOUND", hurst
    else:
        return "REGIME_NEUTRAL", hurst

def detect_liquidity_sweep(klines):
    if not klines or len(klines) < 25:
        return False, ""

    lows_24h = [float(k[3]) for k in klines[-25:-1]]
    low_24h = min(lows_24h)

    last_candle = klines[-1]
    c_open = float(last_candle[1])
    c_high = float(last_candle[2])
    c_low = float(last_candle[3])
    c_close = float(last_candle[4])
    c_vol = float(last_candle[5])

    avg_vol = sum([float(k[5]) for k in klines[-25:-1]]) / 24.0

    swept_support = c_low < low_24h
    closed_above = c_close > low_24h
    lower_wick = min(c_open, c_close) - c_low
    body = abs(c_close - c_open)
    has_rejection_hammer = lower_wick >= 1.3 * body
    volume_surge = c_vol >= 1.3 * avg_vol

    if swept_support and closed_above and has_rejection_hammer and volume_surge:
        return True, f"🔥 SMC Liquidity Sweep: Perfuração do Mínimo de 24h (${low_24h:.2f}) com Pavio de Rejeição e Volume 1.3x Superior!"
    return False, ""

def calculate_relative_strength_rank(multi_klines):
    results = []
    btc_klines = multi_klines.get("BTCUSDT", [])
    if not btc_klines or len(btc_klines) < 25:
        return results

    btc_closes = extract_closes(btc_klines)
    btc_ret = ((btc_closes[-1] - btc_closes[-25]) / btc_closes[-25]) * 100.0

    for symbol, klines in multi_klines.items():
        if not klines or len(klines) < 25:
            continue
        closes = extract_closes(klines)
        asset_ret = ((closes[-1] - closes[-25]) / closes[-25]) * 100.0
        rs_ratio = asset_ret - btc_ret
        rsi_val = calculate_rsi(closes)
        adx_val = calculate_adx(klines)

        combined_score = (rs_ratio * 0.40) + ((100.0 - rsi_val) * 0.40) + (adx_val * 0.20)
        results.append({
            'symbol': symbol,
            'price': closes[-1],
            'rs_ratio': rs_ratio,
            'rsi': rsi_val,
            'score': combined_score
        })

    return sorted(results, key=lambda x: x['score'], reverse=True)

def analyze_futures_squeeze_potential(futures_data, smc_sweep_active=False):
    if not futures_data:
        return False, "Sem dados de mercado derivativo."
        
    funding_rate = futures_data.get('funding_rate', 0.0)
    funding_pct = futures_data.get('funding_rate_pct', 0.0)
    is_short_heavy = futures_data.get('is_short_heavy', False)
    
    if is_short_heavy and smc_sweep_active:
        return True, f"🔥 POTENCIAL SHORT SQUEEZE DETECTADO! Funding Rate negativo ({funding_pct:.4f}%) + SMC Liquidity Sweep!"
    elif is_short_heavy:
        return True, f"⚡ Acúmulo de Shorts detectado no mercado futuro (Funding: {funding_pct:.4f}%)."
    elif funding_rate < 0:
        return False, f"Funding Rate levemente negativo ({funding_pct:.4f}%)."
    else:
        return False, f"Funding Rate neutro/positivo ({funding_pct:.4f}%)."

def calculate_orderbook_imbalance(order_book):
    if not order_book or 'bids' not in order_book or 'asks' not in order_book:
        return 1.0, 0.0, 0.0, False, "Livro de ofertas indisponível."
        
    bids = order_book.get('bids', [])
    asks = order_book.get('asks', [])
    
    total_bid_vol = sum([float(b[1]) for b in bids[:20]])
    total_ask_vol = sum([float(a[1]) for a in asks[:20]])
    
    if total_ask_vol == 0:
        ratio = 5.0
    else:
        ratio = total_bid_vol / total_ask_vol
        
    has_buy_wall = ratio >= 1.5
    
    if ratio >= 2.0:
        msg = f"🛡️ MURO DE COMPRA DE BALEIA DETECTADO! Razão Bids/Asks: {ratio:.2f}x (Suporte Massivo em ${float(bids[0][0]):.2f})."
    elif ratio >= 1.5:
        msg = f"📊 Pressão Compradora no Livro (Bids/Asks: {ratio:.2f}x)."
    else:
        msg = f"Livro de ofertas neutro/vendedor (Bids/Asks: {ratio:.2f}x)."
        
    return ratio, total_bid_vol, total_ask_vol, has_buy_wall, msg

def is_hammer(candle):
    o, h, l, c = float(candle[1]), float(candle[2]), float(candle[3]), float(candle[4])
    body = abs(c - o)
    if body == 0: return False # Exige algum corpo para não ser confundido com Doji
    lower_wick = min(o, c) - l
    upper_wick = h - max(o, c)
    return lower_wick >= 2 * body and upper_wick <= body * 0.5

def is_shooting_star(candle):
    o, h, l, c = float(candle[1]), float(candle[2]), float(candle[3]), float(candle[4])
    body = abs(c - o)
    if body == 0: return False
    upper_wick = h - max(o, c)
    lower_wick = min(o, c) - l
    return upper_wick >= 2 * body and lower_wick <= body * 0.5

def is_bullish_engulfing(prev_c, curr_c):
    po, pc = float(prev_c[1]), float(prev_c[4])
    co, cc = float(curr_c[1]), float(curr_c[4])
    return pc < po and cc > co and cc > po and co < pc

def is_piercing_line(prev_c, curr_c):
    po, pc = float(prev_c[1]), float(prev_c[4])
    co, cc = float(curr_c[1]), float(curr_c[4])
    mid = (po + pc) / 2
    return pc < po and co < pc and cc > mid and cc < po

def is_dark_cloud_cover(prev_c, curr_c):
    po, pc = float(prev_c[1]), float(prev_c[4])
    co, cc = float(curr_c[1]), float(curr_c[4])
    mid = (po + pc) / 2
    return pc > po and co > pc and cc < mid and cc > po

def is_kicker_bullish(prev_c, curr_c):
    po, pc = float(prev_c[1]), float(prev_c[4])
    co, cc = float(curr_c[1]), float(curr_c[4])
    return pc < po and co >= po and cc > co

def is_kicker_bearish(prev_c, curr_c):
    po, pc = float(prev_c[1]), float(prev_c[4])
    co, cc = float(curr_c[1]), float(curr_c[4])
    return pc > po and co <= po and cc < co

def is_long_day(candle):
    o, h, l, c = float(candle[1]), float(candle[2]), float(candle[3]), float(candle[4])
    return (h - l) > 3 * abs(c - o)

def is_short_day(candle):
    o, h, l, c = float(candle[1]), float(candle[2]), float(candle[3]), float(candle[4])
    return (h - l) <= 1.5 * abs(c - o) and abs(c - o) > 0

def is_doji(candle):
    o, c = float(candle[1]), float(candle[4])
    return abs(c - o) <= (float(candle[2]) - float(candle[3])) * 0.1

def is_doji_dragonfly(candle):
    o, h, l, c = float(candle[1]), float(candle[2]), float(candle[3]), float(candle[4])
    return is_doji(candle) and (h - max(o, c)) <= (max(o, c) - l) * 0.1

def is_doji_gravestone(candle):
    o, h, l, c = float(candle[1]), float(candle[2]), float(candle[3]), float(candle[4])
    return is_doji(candle) and (min(o, c) - l) <= (h - min(o, c)) * 0.1

def is_doji_long_shadows(candle):
    o, h, l, c = float(candle[1]), float(candle[2]), float(candle[3]), float(candle[4])
    return is_doji(candle) and (h - l) > 2 * abs(c - o)

def is_bullish_and_bearish_strike(prev_c, curr_c):
    return is_bullish_engulfing(prev_c, curr_c) or is_kicker_bullish(prev_c, curr_c)

def is_rising_three_methods(c1, c2, c3, c4, c5):
    c1o, c1h, c1l, c1c = float(c1[1]), float(c1[2]), float(c1[3]), float(c1[4])
    c5o, c5c = float(c5[1]), float(c5[4])
    if not (c1c > c1o and c5c > c5o and c5c > c1c): return False
    for c in [c2, c3, c4]:
        h, l = float(c[2]), float(c[3])
        if h > c1h or l < c1l: return False
    return True

def is_falling_three_methods(c1, c2, c3, c4, c5):
    c1o, c1h, c1l, c1c = float(c1[1]), float(c1[2]), float(c1[3]), float(c1[4])
    c5o, c5c = float(c5[1]), float(c5[4])
    if not (c1c < c1o and c5c < c5o and c5c < c1c): return False
    for c in [c2, c3, c4]:
        h, l = float(c[2]), float(c[3])
        if h > c1h or l < c1l: return False
    return True

def is_stick_sandwich(c1, c2, c3):
    c1o, c1c = float(c1[1]), float(c1[4])
    c2o, c2c = float(c2[1]), float(c2[4])
    c3o, c3c = float(c3[1]), float(c3[4])
    if not (c1c < c1o and c2c > c2o and c3c < c3o): return False
    return abs(c1c - c3c) / c1c < 0.001

def is_morning_star(c1, c2, c3):
    c1o, c1c = float(c1[1]), float(c1[4])
    c2o, c2h, c2l, c2c = float(c2[1]), float(c2[2]), float(c2[3]), float(c2[4])
    c3o, c3c = float(c3[1]), float(c3[4])
    if not (c1c < c1o and c3c > c3o): return False
    is_c2_small = abs(c2c - c2o) <= (c2h - c2l) * 0.3
    mid_c1 = (c1o + c1c) / 2
    return is_c2_small and c3c > mid_c1

def is_evening_star(c1, c2, c3):
    c1o, c1c = float(c1[1]), float(c1[4])
    c2o, c2h, c2l, c2c = float(c2[1]), float(c2[2]), float(c2[3]), float(c2[4])
    c3o, c3c = float(c3[1]), float(c3[4])
    if not (c1c > c1o and c3c < c3o): return False
    is_c2_small = abs(c2c - c2o) <= (c2h - c2l) * 0.3
    mid_c1 = (c1o + c1c) / 2
    return is_c2_small and c3c < mid_c1

def is_marubozu_bullish(candle):
    o, h, l, c = float(candle[1]), float(candle[2]), float(candle[3]), float(candle[4])
    body = c - o
    if body <= 0: return False
    return (h - c) <= body * 0.05 and (o - l) <= body * 0.05

def is_marubozu_bearish(candle):
    o, h, l, c = float(candle[1]), float(candle[2]), float(candle[3]), float(candle[4])
    body = o - c
    if body <= 0: return False
    return (h - o) <= body * 0.05 and (c - l) <= body * 0.05

def is_tweezer_bottom(c1, c2):
    c1o, c1c, c1l = float(c1[1]), float(c1[4]), float(c1[3])
    c2o, c2c, c2l = float(c2[1]), float(c2[4]), float(c2[3])
    return (c1c < c1o) and (c2c > c2o) and (abs(c1l - c2l) / c1l < 0.001)

def is_tweezer_top(c1, c2):
    c1o, c1c, c1h = float(c1[1]), float(c1[4]), float(c1[2])
    c2o, c2c, c2h = float(c2[1]), float(c2[4]), float(c2[2])
    return (c1c > c1o) and (c2c < c2o) and (abs(c1h - c2h) / c1h < 0.001)

def check_trend(klines):
    closes = extract_closes(klines)
    ema200 = calculate_ema(closes, 200)
    return closes[-1] > ema200

def is_market_downward(klines, period=24):
    closes = extract_closes(klines)
    if len(closes) < period: return False
    
    first_price = closes[-period]
    last_price = closes[-1]
    price_change = ((last_price - first_price) / first_price) * 100
    
    ema50 = calculate_ema(closes, 50)
    ema200 = calculate_ema(closes, 200)
    
    return price_change < -2.0 and closes[-1] < ema50 and ema50 < ema200

def check_candle_patterns(klines):
    if not klines or len(klines) < 5:
        return []

    c1 = klines[-1]
    c2 = klines[-2]
    c3 = klines[-3]
    c4 = klines[-4]
    c5 = klines[-5]

    patterns = []
    if is_hammer(c1): patterns.append("Hammer")
    if is_shooting_star(c1): patterns.append("Shooting Star")
    if is_bullish_engulfing(c2, c1): patterns.append("Bullish Engulfing")
    if is_piercing_line(c2, c1): patterns.append("Piercing Line")
    if is_dark_cloud_cover(c2, c1): patterns.append("Dark Cloud Cover")
    if is_kicker_bullish(c2, c1): patterns.append("Bullish Kicker")
    if is_kicker_bearish(c2, c1): patterns.append("Bearish Kicker")
    if is_long_day(c1): patterns.append("Long Day")
    if is_short_day(c1): patterns.append("Short Day")
    if is_doji(c1): patterns.append("Doji")
    if is_doji_dragonfly(c1): patterns.append("Dragonfly Doji")
    if is_doji_gravestone(c1): patterns.append("Gravestone Doji")
    if is_doji_long_shadows(c1): patterns.append("Long Legged Doji")
    if is_bullish_and_bearish_strike(c2, c1): patterns.append("Three Line Strike")
    if is_rising_three_methods(c5, c4, c3, c2, c1): patterns.append("Rising Three Methods")
    if is_falling_three_methods(c5, c4, c3, c2, c1): patterns.append("Falling Three Methods")
    if is_stick_sandwich(c3, c2, c1): patterns.append("Stick Sandwich")
    if is_morning_star(c3, c2, c1): patterns.append("Morning Star")
    if is_evening_star(c3, c2, c1): patterns.append("Evening Star")
    if is_marubozu_bullish(c1): patterns.append("Bullish Marubozu")
    if is_marubozu_bearish(c1): patterns.append("Bearish Marubozu")
    if is_tweezer_bottom(c2, c1): patterns.append("Tweezer Bottom")
    if is_tweezer_top(c2, c1): patterns.append("Tweezer Top")

    return patterns

def get_candle_details(klines):
    if not klines: return None
    last_candle = klines[-1]
    return {
        'open': float(last_candle[1]),
        'high': float(last_candle[2]),
        'low': float(last_candle[3]),
        'close': float(last_candle[4]),
        'volume': float(last_candle[5]),
    }

def calculate_multi_timeframe_confluence(klines_4h, klines_1h, klines_15m):
    """
    FASE 2 (v4.0): Matriz de Confluência Multi-Timeframe Tridimensional.
    Calcula a pontuação de confluência (0% a 100%) entre 4H (Macro), 1H (Estrutura) e 15M (Trigger).
    Retorna: (score: int, is_confluent: bool, details: dict)
    """
    score = 0
    details = {'4h_score': 0, '1h_score': 0, '15m_score': 0, 'reasons': []}

    # 1. Avaliação 4H (Macro Trend - Peso 40 pontos)
    if klines_4h and len(klines_4h) >= 20:
        c4h = extract_closes(klines_4h)
        last_price_4h = c4h[-1]
        ema50_4h = calculate_ema(c4h, min(50, len(c4h)))
        ema200_4h = calculate_ema(c4h, min(200, len(c4h)))

        if last_price_4h > ema50_4h or len(c4h) < 50:
            score += 20
            details['4h_score'] += 20
            details['reasons'].append("4H: Preço aci/ma da EMA (+20%)")
        if ema50_4h >= ema200_4h or last_price_4h > ema200_4h:
            score += 20
            details['4h_score'] += 20
            details['reasons'].append("4H: Tendência Macro de Alta (+20%)")

    # 2. Avaliação 1H (Estrutura Intermediária & Regime - Peso 30 pontos)
    if klines_1h and len(klines_1h) >= 20:
        c1h = extract_closes(klines_1h)
        rsi_1h = calculate_rsi(c1h)
        regime_1h, _ = detect_market_regime(klines_1h)

        if rsi_1h <= 55:
            score += 15
            details['1h_score'] += 15
            details['reasons'].append(f"1H: RSI saudável ({rsi_1h:.1f}) (+15%)")
        if regime_1h != "REGIME_CRASH_PANIC":
            score += 15
            details['1h_score'] += 15
            details['reasons'].append("1H: Sem Pânico de Queda (+15%)")

    # 3. Avaliação 15M (Gatilho de Execução - Peso 30 pontos)
    if klines_15m and len(klines_15m) >= 15:
        c15m = extract_closes(klines_15m)
        rsi_15m = calculate_rsi(c15m)
        macd_15m, sig_15m = calculate_macd(c15m)

        if rsi_15m <= 45:
            score += 15
            details['15m_score'] += 15
            details['reasons'].append(f"15M: Gatilho Sobrevendido (RSI={rsi_15m:.1f}) (+15%)")
        if macd_15m >= sig_15m or rsi_15m <= 35:
            score += 15
            details['15m_score'] += 15
            details['reasons'].append("15M: Crossover/Momentum Positivo (+15%)")

    is_confluent = score >= 70
    return score, is_confluent, details

def detect_orderbook_whale_walls(orderbook, current_price, raw_tp_price, raw_sl_price, tick_size=0.0001):
    """
    FASE 3 (v4.0): Orderbook 50 Depth & Whale Wall TP/SL Protection.
    Escaneia os 50 níveis do Livro de Ofertas da Binance e ajusta o Take Profit
    para ficar 0.15% ANTES de muros de venda massivos de baleias.
    Retorna: (adjusted_tp, adjusted_sl, wall_detected, wall_info)
    """
    if not orderbook or 'asks' not in orderbook or not orderbook['asks']:
        return raw_tp_price, raw_sl_price, False, None

    asks = orderbook.get('asks', [])
    if len(asks) < 5:
        return raw_tp_price, raw_sl_price, False, None

    ask_levels = []
    total_qty = 0.0
    for p_str, q_str in asks[:50]:
        p = float(p_str)
        q = float(q_str)
        val = p * q
        ask_levels.append({'price': p, 'qty': q, 'usdt': val})
        total_qty += q

    avg_qty = total_qty / len(ask_levels) if ask_levels else 1.0
    wall_threshold = avg_qty * 3.0

    adjusted_tp = raw_tp_price
    adjusted_sl = raw_sl_price
    wall_detected = False
    wall_info = None

    for level in ask_levels:
        wall_p = level['price']
        wall_q = level['qty']
        
        if wall_p > current_price and wall_p <= raw_tp_price * 1.002:
            if wall_q >= wall_threshold or level['usdt'] >= 25000:
                safe_tp = wall_p * 0.9985
                if safe_tp > current_price * 1.01:
                    adjusted_tp = safe_tp
                    wall_detected = True
                    wall_info = {
                        'wall_price': wall_p,
                        'wall_qty': wall_q,
                        'wall_usdt': level['usdt'],
                        'original_tp': raw_tp_price,
                        'adjusted_tp': adjusted_tp
                    }
                    break

    return adjusted_tp, adjusted_sl, wall_detected, wall_info

def calculate_atr(klines, period=14):
    """
    FASE 4 (v4.0): Indicador ATR (Average True Range).
    Calcula o True Range e o ATR percentual para medição da volatilidade real do ativo.
    Retorna: (atr_value: float, atr_pct: float)
    """
    if not klines or len(klines) < period + 1:
        return 0.0, 0.02

    highs = [float(k[2]) for k in klines]
    lows = [float(k[3]) for k in klines]
    closes = [float(k[4]) for k in klines]

    tr_list = []
    for i in range(1, len(klines)):
        h = highs[i]
        l = lows[i]
        prev_c = closes[i-1]
        tr = max(h - l, abs(h - prev_c), abs(l - prev_c))
        tr_list.append(tr)

    if not tr_list:
        return 0.0, 0.02

    atr_value = float(pd.Series(tr_list).rolling(window=period).mean().iloc[-1])
    last_close = closes[-1]
    atr_pct = (atr_value / last_close) if last_close > 0 else 0.02

    return atr_value, atr_pct

def calculate_fibonacci_supports(klines, period=50):
    """
    FASE 1 (v5.0): Cálculo de Suportes de Fibonacci Institucionais.
    Identifica a máxima e a mínima recente da tendência e calcula os níveis de retração de 61.8% e 78.6%.
    Retorna: (fib_618: float, fib_786: float, swing_high: float, swing_low: float)
    """
    if not klines or len(klines) < 20:
        return 0.0, 0.0, 0.0, 0.0

    recent_klines = klines[-period:] if len(klines) >= period else klines
    highs = [float(k[2]) for k in recent_klines]
    lows = [float(k[3]) for k in recent_klines]

    swing_high = max(highs)
    swing_low = min(lows)
    diff = swing_high - swing_low

    fib_618 = swing_high - (diff * 0.618)
    fib_786 = swing_high - (diff * 0.786)

    return fib_618, fib_786, swing_high, swing_low

def calculate_lead_lag_alpha(btc_klines, altcoin_klines):
    """
    FASE 2 (v5.0): Motor de Antecipação (Lead-Lag Alpha Engine).
    Avalia a aceleração de preço/volume do BTCUSDT nos últimos 60 a 180 segundos
    e verifica se há divergência favorável (Lag) na altcoin do Top 40.
    Retorna: (is_btc_lead: bool, btc_impulse_pct: float, alpha_msg: str)
    """
    if not btc_klines or len(btc_klines) < 3:
        return False, 0.0, "Sem dados BTC 1m"

    btc_closes = [float(k[4]) for k in btc_klines]
    btc_vols = [float(k[5]) for k in btc_klines]

    btc_cur = btc_closes[-1]
    btc_prev = btc_closes[-3] if len(btc_closes) >= 3 else btc_closes[0]
    btc_change_pct = ((btc_cur - btc_prev) / btc_prev) * 100.0 if btc_prev > 0 else 0.0

    vol_series = pd.Series(btc_vols)
    avg_vol = vol_series.mean() if len(vol_series) > 0 else 1.0
    cur_vol = btc_vols[-1]
    is_volume_spike = cur_vol >= avg_vol * 1.5

    alt_change_pct = 0.0
    if altcoin_klines and len(altcoin_klines) >= 3:
        alt_closes = [float(k[4]) for k in altcoin_klines]
        alt_cur = alt_closes[-1]
        alt_prev = alt_closes[-3] if len(alt_closes) >= 3 else alt_closes[0]
        alt_change_pct = ((alt_cur - alt_prev) / alt_prev) * 100.0 if alt_prev > 0 else 0.0

    if btc_change_pct >= 0.25 and is_volume_spike and alt_change_pct <= (btc_change_pct * 0.7):
        return True, btc_change_pct, f"🔥 LEAD-LAG ALPHA: BTC IMPULSE (+{btc_change_pct:.2f}% em 3m)"

    return False, btc_change_pct, "Sem impulso de antecipação BTC"

def calculate_cvd_trend(trades_data):
    """
    FASE 3 (v5.0): Cumulative Volume Delta (CVD Tape Reading Engine).
    Calcula a diferença acumulada entre o volume de compras a mercado e vendas a mercado.
    Retorna: (cvd_usdt: float, buy_ratio_pct: float, is_bullish_cvd: bool)
    """
    if not trades_data:
        return 0.0, 50.0, False

    buy_vol_usdt = 0.0
    sell_vol_usdt = 0.0

    for t in trades_data:
        p = float(t.get('price', 0))
        q = float(t.get('qty', 0))
        val = p * q
        is_buyer_maker = t.get('isBuyerMaker', False)

        if is_buyer_maker:
            sell_vol_usdt += val
        else:
            buy_vol_usdt += val

    total_vol = buy_vol_usdt + sell_vol_usdt
    cvd_usdt = buy_vol_usdt - sell_vol_usdt
    buy_ratio_pct = (buy_vol_usdt / total_vol) * 100.0 if total_vol > 0 else 50.0

    is_bullish_cvd = (buy_ratio_pct >= 60.0) and (cvd_usdt > 0)

    return cvd_usdt, buy_ratio_pct, is_bullish_cvd

def calculate_pair_cointegration_zscore(klines_a, klines_b, period=50):
    """
    FASE 4 (v5.0): Cointegration Pair Trading & Statistical Arbitrage.
    Calcula o Z-Score do spread da razão de preços entre o Ativo A e o Ativo B de referência.
    Retorna: (z_score: float, is_stat_arb_buy: bool, ratio_mean: float, ratio_std: float)
    """
    if not klines_a or not klines_b or len(klines_a) < period or len(klines_b) < period:
        return 0.0, False, 0.0, 0.0

    closes_a = extract_closes(klines_a[-period:])
    closes_b = extract_closes(klines_b[-period:])

    min_len = min(len(closes_a), len(closes_b))
    closes_a = closes_a[-min_len:]
    closes_b = closes_b[-min_len:]

    ratios = []
    for ca, cb in zip(closes_a, closes_b):
        if cb > 0:
            ratios.append(ca / cb)

    if not ratios or len(ratios) < 20:
        return 0.0, False, 0.0, 0.0

    series_ratios = pd.Series(ratios)
    mean_val = series_ratios.mean()
    std_val = series_ratios.std()

    if std_val == 0:
        return 0.0, False, mean_val, 0.0

    current_ratio = ratios[-1]
    z_score = (current_ratio - mean_val) / std_val

    is_stat_arb_buy = (z_score <= -2.0)

    return float(z_score), is_stat_arb_buy, float(mean_val), float(std_val)
