import asyncio

async def evaluate_smart_money(client, symbol, period='15m', log=print):
    """
    Inteligência Institucional de Smart Money & Baleias (Evolução v3).
    Avalia:
    1. Top Trader Long/Short Position Ratio (20% maiores contas com mais capital).
    2. Global Long/Short Account Ratio (Varejo / Todas as contas).
    3. Taker Long/Short Volume Ratio (Agressão de ordens a mercado).
    4. Divergência Baleias vs Varejo (Smart Money vs Retail Divergence).

    Retorna:
    - bias_direction: 'LONG', 'SHORT' ou 'NEUTRAL'
    - sm_score: int (0 a 35 pontos de confluência)
    - sm_metrics: dict com todos os ratios e diagnóstico institucional
    """
    try:
        tasks = [
            client.futures_top_longshort_position_ratio(symbol=symbol, period=period, limit=1),
            client.futures_global_longshort_ratio(symbol=symbol, period=period, limit=1),
            client.futures_taker_longshort_ratio(symbol=symbol, period=period, limit=1)
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        top_pos = results[0]
        global_acc = results[1]
        taker_vol = results[2]
        
        top_ratio = 1.0
        if not isinstance(top_pos, Exception) and top_pos and len(top_pos) > 0:
            top_ratio = float(top_pos[-1].get('longShortRatio', 1.0))
            
        global_ratio = 1.0
        if not isinstance(global_acc, Exception) and global_acc and len(global_acc) > 0:
            global_ratio = float(global_acc[-1].get('longShortRatio', 1.0))
            
        taker_ratio = 1.0
        if not isinstance(taker_vol, Exception) and taker_vol and len(taker_vol) > 0:
            taker_ratio = float(taker_vol[-1].get('buySellRatio', 1.0))
            
        sm_metrics = {
            'top_ratio': top_ratio,
            'global_ratio': global_ratio,
            'taker_ratio': taker_ratio,
            'divergence': 'NORMAL',
            'summary': 'Neutro'
        }
        
        sm_score_long = 0
        sm_score_short = 0
        
        # 1. Avaliação do Top Trader Position Ratio (Peso de até 15 pts)
        if top_ratio >= 1.35:
            sm_score_long += 15
        elif top_ratio >= 1.15:
            sm_score_long += 10
        elif top_ratio <= 0.75:
            sm_score_short += 15
        elif top_ratio <= 0.85:
            sm_score_short += 10
            
        # 2. Avaliação do Taker Volume Ratio - Agressão a Mercado (Peso de até 10 pts)
        if taker_ratio >= 1.20:
            sm_score_long += 10
        elif taker_ratio >= 1.05:
            sm_score_long += 5
        elif taker_ratio <= 0.80:
            sm_score_short += 10
        elif taker_ratio <= 0.95:
            sm_score_short += 5
            
        # 3. Divergência Smart Money vs Varejo (Gatilho de Ouro - Peso de até 15 pts)
        # Bullish Absorption: Varejo em pânico/vendido, mas Baleias comprando
        if global_ratio <= 0.95 and top_ratio >= 1.20:
            sm_score_long += 15
            sm_metrics['divergence'] = 'BULLISH_ABSORPTION'
            sm_metrics['summary'] = f'🐋 Absorção Bullish (Baleias {top_ratio:.2f} vs Varejo {global_ratio:.2f})'
            
        # Bearish Distribution: Varejo eufórico/comprando topo, mas Baleias desovando
        elif global_ratio >= 1.25 and top_ratio <= 0.85:
            sm_score_short += 15
            sm_metrics['divergence'] = 'BEARISH_DISTRIBUTION'
            sm_metrics['summary'] = f'🐋 Distribuição Bearish (Varejo Eufórico {global_ratio:.2f} vs Baleias {top_ratio:.2f})'
        else:
            if top_ratio > 1.15:
                sm_metrics['summary'] = f'Baleias Compradas ({top_ratio:.2f}x)'
            elif top_ratio < 0.85:
                sm_metrics['summary'] = f'Baleias Vendidas ({top_ratio:.2f}x)'
            else:
                sm_metrics['summary'] = f'Equilíbrio ({top_ratio:.2f}x)'

        # Decisão de Viés Direcional
        if sm_score_long >= 15 and sm_score_long > sm_score_short:
            return 'LONG', sm_score_long, sm_metrics
        elif sm_score_short >= 15 and sm_score_short > sm_score_long:
            return 'SHORT', sm_score_short, sm_metrics
            
        return 'NEUTRAL', max(sm_score_long, sm_score_short), sm_metrics
        
    except Exception as e:
        log(f"⚠️ Erro ao avaliar Smart Money para {symbol}: {e}")
        return 'NEUTRAL', 0, {'top_ratio': 1.0, 'global_ratio': 1.0, 'taker_ratio': 1.0, 'divergence': 'ERROR', 'summary': 'Erro na leitura'}
