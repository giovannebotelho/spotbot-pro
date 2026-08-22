import asyncio
import pandas as pd
from core.indicators import calculate_rsi
from services.binance_client import get_futures_klines

async def evaluate_macro_regime(client, symbol, log=print):
    """
    Avalia a saúde macro do mercado em múltiplos timeframes (1D - Diário e 4H).
    Detecta:
    1. Exaustão Parabólica de Topo (RSI 1D > 75 ou esticamento > 12% da EMA20 1D).
    2. Divergência de Volume no Topo (Preço subindo mas volume caindo nos últimos dias).
    3. Exaustão de Fundo / Capitulação (RSI 1D < 25 ou esticamento > 15% abaixo da EMA20 1D).
    
    Retorna:
    - allow_long: bool
    - allow_short: bool
    - reason: str
    - macro_score: dict
    """
    allow_long = True
    allow_short = True
    reason = "Neutro / Equilibrado"
    
    try:
        # 1. Busca Klines Diárias (1D) dos últimos 30 dias para o ativo e para o BTC
        klines_1d = await get_futures_klines(client, symbol, interval='1d', limit=35)
        btc_klines_1d = await get_futures_klines(client, 'BTCUSDT', interval='1d', limit=35)
        
        if not klines_1d or len(klines_1d) < 20:
            return True, True, "Histórico 1D insuficiente", {}
            
        closes_1d = [float(k[4]) for k in klines_1d]
        volumes_1d = [float(k[5]) for k in klines_1d]
        cur_price = closes_1d[-1]
        
        # RSI Diário
        rsi_1d = calculate_rsi(closes_1d)
        
        # EMA20 Diária
        df_1d = pd.DataFrame({'close': closes_1d, 'volume': volumes_1d})
        ema20_1d = df_1d['close'].ewm(span=20, adjust=False).mean().iloc[-1]
        dist_ema20_pct = ((cur_price - ema20_1d) / ema20_1d) * 100
        
        # Variação acumulada nos últimos 3 dias
        var_3d_pct = ((cur_price - closes_1d[-4]) / closes_1d[-4]) * 100 if len(closes_1d) >= 4 else 0.0
        
        # Análise de Volume Diário (Média de 7 dias vs Último dia)
        vol_avg_7d = df_1d['volume'].iloc[-8:-1].mean() if len(df_1d) >= 8 else df_1d['volume'].mean()
        cur_vol = df_1d['volume'].iloc[-1]
        vol_decay = (cur_vol < vol_avg_7d * 0.75) and var_3d_pct > 8.0
        
        # BTC Macro Check
        btc_rsi_1d = 50.0
        btc_var_3d = 0.0
        if btc_klines_1d and len(btc_klines_1d) >= 20:
            btc_closes_1d = [float(k[4]) for k in btc_klines_1d]
            btc_rsi_1d = calculate_rsi(btc_closes_1d)
            btc_var_3d = ((btc_closes_1d[-1] - btc_closes_1d[-4]) / btc_closes_1d[-4]) * 100 if len(btc_closes_1d) >= 4 else 0.0

        macro_data = {
            'rsi_1d': rsi_1d,
            'dist_ema20_1d': dist_ema20_pct,
            'var_3d_pct': var_3d_pct,
            'btc_rsi_1d': btc_rsi_1d,
            'btc_var_3d': btc_var_3d,
            'vol_decay': vol_decay
        }
        
        # -------------------------------------------------------------
        # 🚨 CENÁRIO 1: EXAUSTÃO PARABÓLICA DE TOPO (RISCO DE DUMP / LIQUIDAÇÃO)
        # -------------------------------------------------------------
        # Se o BTC ou o Ativo subiram > 12% em 3 dias com RSI Diário > 74 OU afastamento > 12% da média diária:
        if rsi_1d >= 74.0 or btc_rsi_1d >= 75.0 or dist_ema20_pct >= 12.0 or var_3d_pct >= 15.0:
            allow_long = False
            reason = f"🛡️ [MACRO 1D] Exaustão de Topo Parabólico! RSI 1D: {rsi_1d:.1f} (BTC: {btc_rsi_1d:.1f}) | Dist EMA20: +{dist_ema20_pct:.1f}% | Alta 3D: +{var_3d_pct:.1f}%. Risco alto de liquidação de longs!"
            return allow_long, allow_short, reason, macro_data
            
        # Perda de Volume no Topo (Divergência de Exaustão)
        if vol_decay and rsi_1d >= 68.0:
            allow_long = False
            reason = f"🛡️ [DIVERGÊNCIA VOLUME 1D] Preço esticado (+{var_3d_pct:.1f}%) porém volume de compra secando. Bloqueando LONG no topo!"
            return allow_long, allow_short, reason, macro_data

        # -------------------------------------------------------------
        # 🚨 CENÁRIO 2: CAPITULAÇÃO / QUEDA LIVRE MACRO
        # -------------------------------------------------------------
        if rsi_1d <= 26.0 or dist_ema20_pct <= -15.0 or var_3d_pct <= -18.0:
            allow_short = False
            reason = f"🛡️ [MACRO 1D] Exaustão de Fundo/Capitulação! RSI 1D: {rsi_1d:.1f} | Dist EMA20: {dist_ema20_pct:.1f}%. Risco de repique violento contra Short!"
            return allow_long, allow_short, reason, macro_data

        return True, True, "Regime Macro Saudável", macro_data

    except Exception as e:
        return True, True, f"Aviso na análise macro: {e}", {}
