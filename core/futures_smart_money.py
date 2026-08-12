import asyncio

async def evaluate_smart_money(client, symbol, period='5m', log=print):
    """
    Filtro de Confluência: Smart Money & Baleias
    Avalia o Top Trader Long/Short Ratio e o Taker Volume Ratio.
    Retorna: 'LONG', 'SHORT' ou 'NEUTRAL'
    """
    try:
        # Busca o Long/Short Ratio de posições dos Top Traders
        # Retorna lista com {symbol, longShortRatio, longAccount, shortAccount, timestamp}
        tasks = [
            client.futures_top_longshort_position_ratio(symbol=symbol, period=period, limit=1),
            client.futures_taker_longshort_ratio(symbol=symbol, period=period, limit=1)
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        top_pos = results[0]
        taker_vol = results[1]
        
        top_ratio = 1.0
        if not isinstance(top_pos, Exception) and top_pos and len(top_pos) > 0:
            top_ratio = float(top_pos[-1].get('longShortRatio', 1.0))
            
        taker_ratio = 1.0
        if not isinstance(taker_vol, Exception) and taker_vol and len(taker_vol) > 0:
            taker_ratio = float(taker_vol[-1].get('buySellRatio', 1.0))
            
        # Avaliação agressiva: 
        # > 1.05 = Tendência compradora forte
        # < 0.95 = Tendência vendedora forte
        
        # Apenas Top Traders positions é um proxy forte de baleias alocadas
        if top_ratio > 1.10 and taker_ratio > 1.05:
            return 'LONG', top_ratio, taker_ratio
        elif top_ratio < 0.90 and taker_ratio < 0.95:
            return 'SHORT', top_ratio, taker_ratio
        elif top_ratio > 1.15: # Muito pesado em Long
            return 'LONG', top_ratio, taker_ratio
        elif top_ratio < 0.85: # Muito pesado em Short
            return 'SHORT', top_ratio, taker_ratio
            
        return 'NEUTRAL', top_ratio, taker_ratio
        
    except Exception as e:
        log(f"⚠️ Erro ao avaliar Smart Money para {symbol}: {e}")
        return 'NEUTRAL', 1.0, 1.0
