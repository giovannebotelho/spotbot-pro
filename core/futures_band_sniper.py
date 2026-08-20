import time
import pandas as pd
from core.indicators import calculate_rsi

_klines_15m_cache = {}

def calculate_atr_locally(df, period=14):
    """
    Calcula o Average True Range (ATR)
    df deve conter 'high', 'low', 'close'
    """
    if len(df) < period + 1:
        return 0.0
    
    high = df['high']
    low = df['low']
    close = df['close'].shift(1)
    
    tr1 = high - low
    tr2 = (high - close).abs()
    tr3 = (low - close).abs()
    
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = tr.rolling(window=period).mean()
    
    return float(atr.iloc[-1])

async def evaluate_band_sniper(client, symbol, log=print):
    """
    Avalia a estratégia de Reversão à Média no timeframe de 15m.
    Toca/Rompe Bandas de Bollinger (20,2) extremas e busca a SMA 20 como alvo.
    """
    global _klines_15m_cache
    now = time.time()
    
    # Cache para evitar over-fetching se o scanner varrer rápido
    if symbol in _klines_15m_cache and now - _klines_15m_cache[symbol]['timestamp'] < 30:
        klines = _klines_15m_cache[symbol]['klines']
    else:
        try:
            klines = await client.futures_klines(symbol=symbol, interval='15m', limit=50)
            if not klines or len(klines) < 30:
                return None, None, None
            _klines_15m_cache[symbol] = {'klines': klines, 'timestamp': now}
        except Exception as e:
            log(f"⚠️ Erro ao buscar klines 15m para Band Sniper em {symbol}: {e}")
            return None, None, None

    try:
        closes = [float(k[4]) for k in klines]
        highs = [float(k[2]) for k in klines]
        lows = [float(k[3]) for k in klines]
        
        df = pd.DataFrame({'high': highs, 'low': lows, 'close': closes})
        
        # Cálculo das Bandas de Bollinger (20, 2)
        sma20 = df['close'].rolling(window=20).mean()
        std20 = df['close'].rolling(window=20).std()
        
        ub = sma20 + 2 * std20
        lb = sma20 - 2 * std20
        
        rsi = calculate_rsi(closes, period=14)
        atr = calculate_atr_locally(df, period=14)
        
        if pd.isna(ub.iloc[-1]) or pd.isna(lb.iloc[-1]) or atr == 0.0:
            return None, None, None
            
        cur_close = closes[-1]
        cur_high = highs[-1]
        cur_low = lows[-1]
        
        cur_ub = ub.iloc[-1]
        cur_lb = lb.iloc[-1]
        cur_sma = sma20.iloc[-1]
        
        prev_close = closes[-2]
        prev_high = highs[-2]
        prev_low = lows[-2]
        
        # 1. Sinal SHORT: Rompimento Real do Teto (Exaustão Extrema) com RSI Sobrecomprado Real (>= 74)
        is_upper_pierced = cur_high >= cur_ub or prev_high >= cur_ub
        b_pct_upper = (cur_close - cur_lb) / (cur_ub - cur_lb) if (cur_ub - cur_lb) > 0 else 0
        
        # Só dá SHORT se houve rejeição de topo (fechamento abaixo da máxima ou vela vermelha) e RSI parabólico
        is_exhaustion_short = cur_close < cur_high and (cur_close <= prev_close or cur_close < cur_ub)
        
        if (is_upper_pierced or b_pct_upper >= 1.02) and rsi >= 74 and is_exhaustion_short:
            direction = 'SHORT'
            tp_price = cur_sma
            # SL = 1.5 * ATR acima da máxima extrema (wick)
            extreme_high = max(cur_high, prev_high)
            sl_price = extreme_high + (1.5 * atr)
            return direction, tp_price, sl_price
            
        # 2. Sinal LONG: Rompimento Real do Fundo (Exaustão Extrema) com RSI Sobrevendido Real (<= 26)
        is_lower_pierced = cur_low <= cur_lb or prev_low <= cur_lb
        b_pct_lower = (cur_close - cur_lb) / (cur_ub - cur_lb) if (cur_ub - cur_lb) > 0 else 0
        
        # Só dá LONG se houve absorção de fundo (fechamento acima da mínima) e RSI em capitulação
        is_exhaustion_long = cur_close > cur_low and (cur_close >= prev_close or cur_close > cur_lb)
        
        if (is_lower_pierced or b_pct_lower <= -0.02) and rsi <= 26 and is_exhaustion_long:
            direction = 'LONG'
            tp_price = cur_sma
            # SL = 1.5 * ATR abaixo da mínima extrema (wick)
            extreme_low = min(cur_low, prev_low)
            sl_price = extreme_low - (1.5 * atr)
            return direction, tp_price, sl_price

    except Exception as e:
        log(f"⚠️ Erro ao calcular indicadores no Band Sniper para {symbol}: {e}")
        
    return None, None, None
