import asyncio
from config.settings import TELEGRAM_CONFIG, TIMEZONE
from core.futures_state import futures_state
from services.binance_client import (
    setup_futures_margin, place_futures_order, place_futures_conditional_order,
    get_futures_usdt_balance, get_futures_klines, get_futures_whale_ratio
)
from core.decision import get_precision
from core.indicators import calculate_rsi, check_candle_patterns, calculate_macd, calculate_atr
from core.futures_order_manager import monitor_futures_lifecycle
from services.telegram_notifier import send_telegram_message
from config.settings import TOP_10_FUTURES_SYMBOLS

bot_futures_running = False
bot_futures_status_data = {
    "price": 0, "symbol": "", "action": "Aguardando...", "target_asset": "BTCUSDT",
    "active_symbols": [], "active_positions": futures_state.get_all_sync()
}

shared_futures_market_data = {
    'dates': [], 'klines': [], 'bb_upper': [], 'bb_lower': [], 'ema200': [], 'volumes': []
}

async def run_futures_bot(client, bsm, db, log=print, status=print):
    global bot_futures_running, bot_futures_status_data
    bot_futures_running = True
    log("🚀 Iniciando Motor de Futuros (HedgeFund Edition)...")
    
    try:
        usdt_balance = await get_futures_usdt_balance(client)
        log(f"💰 Saldo USDT Futuros: \033[1;32m${usdt_balance:.2f}\033[0m")
        from config.settings import TELEGRAM_CONFIG
        if TELEGRAM_CONFIG.get('bot_token') and TELEGRAM_CONFIG.get('chat_id'):
            asyncio.create_task(send_telegram_message(
                TELEGRAM_CONFIG['bot_token'], TELEGRAM_CONFIG['chat_id'],
                f"<b>🚀 Motor de Futuros Iniciado! 🚀</b>\n\n"
                f"💰 Saldo USDT Futuros: <b>${usdt_balance:.2f}</b>\n"
            ))
    except Exception as e:
        log(f"⚠️ Erro ao buscar saldo inicial de Futuros: {e}")
        
    from core.futures_order_manager import run_fallback_position_monitor
    from services.binance_futures_stream import run_futures_user_stream
    from core.futures_trailing_lock import run_trailing_lock_monitor
    asyncio.create_task(run_futures_user_stream(client, db, log))
    asyncio.create_task(run_fallback_position_monitor(client, db, log))
    asyncio.create_task(run_trailing_lock_monitor(client, log))
    
    symbols_to_scan = TOP_10_FUTURES_SYMBOLS
    
    try:
        exchange_info = await client.futures_exchange_info()
        symbols_info = {s['symbol']: s for s in exchange_info['symbols']}
    except Exception as e:
        log(f"⚠️ Erro ao buscar futures_exchange_info: {e}")
        symbols_info = {}
        
    try:
        positions = await client.futures_position_information()
        active = [p for p in positions if float(p['positionAmt']) != 0]
        if active:
            log(f"🔄 \033[1;36mState Recovery Engine\033[0m: {len(active)} posição(ões) de Futuros ativa(s) encontrada(s)!")
            for p in active:
                rec_symbol = p['symbol']
                qty = float(p['positionAmt'])
                entry_price = float(p['entryPrice'])
                direction = 'LONG' if qty > 0 else 'SHORT'
                qty = abs(qty)
                
                algo_open = await client.futures_get_open_algo_orders(symbol=rec_symbol)
                tp_order = next((o for o in algo_open if o['orderType'] == 'TAKE_PROFIT_MARKET'), None)
                sl_order = next((o for o in algo_open if o['orderType'] == 'STOP_MARKET'), None)
                
                tp_price = float(tp_order['triggerPrice']) if tp_order else (entry_price * 1.03 if direction == 'LONG' else entry_price * 0.97)
                sl_price = float(sl_order['triggerPrice']) if sl_order else (entry_price * 0.98 if direction == 'LONG' else entry_price * 1.02)
                
                if tp_order and sl_order:
                    await futures_state.add(rec_symbol, {
                        'entry': entry_price, 'tp': tp_price, 'sl': sl_price, 'direction': direction
                    })
                    bot_futures_status_data['active_symbols'] = list((await futures_state.get_all()).keys())
                    bot_futures_status_data['target_asset'] = rec_symbol
                    
                    try:
                        import pandas as pd
                        import datetime as dt_module
                        from config.settings import TIMEZONE, TRADING_CONFIG
                        klines_raw = await get_futures_klines(client, symbol=rec_symbol, interval=TRADING_CONFIG['interval'], limit=100)
                        if klines_raw:
                            klines_rec = [float(k[4]) for k in klines_raw]
                            dates_rec = [dt_module.datetime.fromtimestamp(float(k[0])/1000).strftime('%H:%M') for k in klines_raw]
                            volumes_rec = [float(k[5]) for k in klines_raw]
                            
                            df_rec = pd.DataFrame({'close': klines_rec})
                            sma20 = df_rec['close'].rolling(window=20).mean()
                            std20 = df_rec['close'].rolling(window=20).std()
                            bb_upper = (sma20 + 2 * std20).where(pd.notnull(sma20), None).tolist()
                            bb_lower = (sma20 - 2 * std20).where(pd.notnull(sma20), None).tolist()
                            ema200 = df_rec['close'].ewm(span=min(200, len(klines_rec)), adjust=False).mean().where(pd.notnull(df_rec['close']), None).tolist()

                            shared_futures_market_data['dates'] = dates_rec
                            shared_futures_market_data['klines'] = [[float(k[1]), float(k[4]), float(k[3]), float(k[2])] for k in klines_raw]
                            shared_futures_market_data['bb_upper'] = bb_upper[-100:]
                            shared_futures_market_data['bb_lower'] = bb_lower[-100:]
                            shared_futures_market_data['ema200'] = ema200[-100:]
                            shared_futures_market_data['volumes'] = volumes_rec
                    except Exception as k_err:
                        log(f"⚠️ Aviso ao carregar klines no State Recovery Futuros: {k_err}")

                    log(f"🛡️ Retomando monitoramento de \033[1;33m{rec_symbol}\033[0m sem cancelar a operação...")
                    asyncio.create_task(monitor_futures_lifecycle(
                        client, bsm, rec_symbol, direction, entry_price, qty,
                        tp_order.get('algoId'), sl_order.get('algoId'), tp_price, sl_price, db,
                        bot_futures_status_data, log, status
                    ))
                    if TELEGRAM_CONFIG.get('bot_token') and TELEGRAM_CONFIG.get('chat_id'):
                        asyncio.create_task(send_telegram_message(
                            TELEGRAM_CONFIG['bot_token'], TELEGRAM_CONFIG['chat_id'],
                            f"🔄 <b>FUTURES State Recovery Ativado!</b>\n\n"
                            f"🪙 Par: <b>{rec_symbol}</b> ({direction})\n"
                            f"🛡️ Posição adotada da nuvem para monitoramento ativo!"
                        ))
                else:
                    log(f"⚠️ Posição órfã em {rec_symbol} sem Stop Loss! Recomenda-se fechar manualmente.")
    except Exception as e:
        log(f"⚠️ Erro no State Recovery do Futuros: {e}")

    
    while bot_futures_running:
        try:
            active_positions = await futures_state.get_all()
            if len(active_positions) >= 3:
                status("⏳ Limite de 3 posições simultâneas atingido no Futuros.")
                await asyncio.sleep(10)
                continue
                
            usdt_balance = await get_futures_usdt_balance(client)
            if usdt_balance < 20.0:
                log(f"⚠️ Saldo insuficiente no Futuros: ${usdt_balance:.2f}. Mínimo $20. Standby por 5 minutos...")
                await asyncio.sleep(300)
                continue
                
            try:
                account_info = await client.futures_account()
                binance_open_positions = [p['symbol'] for p in account_info.get('positions', []) if float(p.get('positionAmt', 0)) != 0]
            except Exception as e:
                log(f"⚠️ Erro ao checar posições abertas na Binance: {e}")
                binance_open_positions = []
                
            # --- FILTRO DE REGIME DE MERCADO (BTCUSDT) ---
            btc_trend = 'NEUTRAL'
            btc_rsi = 50.0
            try:
                btc_klines = await get_futures_klines(client, "BTCUSDT", interval="15m", limit=30)
                if btc_klines and len(btc_klines) >= 20:
                    import pandas as pd
                    btc_closes = [float(k[4]) for k in btc_klines]
                    from core.indicators import calculate_rsi
                    btc_rsi = calculate_rsi(btc_closes)
                    btc_df = pd.DataFrame({'close': btc_closes})
                    btc_ema20 = btc_df['close'].ewm(span=20, adjust=False).mean().tolist()[-1]
                    btc_cur = btc_closes[-1]
                    
                    if btc_cur < btc_ema20 and btc_rsi < 45:
                        btc_trend = 'BEAR'
                    elif btc_cur > btc_ema20 and btc_rsi > 55:
                        btc_trend = 'BULL'
                        
                status(f"🌍 Market Regime (BTC): {btc_trend} (RSI: {btc_rsi:.1f})")
            except Exception as e:
                log(f"⚠️ Erro ao analisar BTCUSDT para filtro de mercado: {e}")
            # ---------------------------------------------

            for symbol in symbols_to_scan:
                active_positions = await futures_state.get_all()
                if symbol in active_positions or symbol in binance_open_positions:
                    continue
                    
                active_positions = await futures_state.get_all()
                if len(active_positions) >= 3:
                    break
                    
                status(f"🔍 [FUTUROS] Analisando {symbol}...")
                
                try:
                    # Fetch Klines 15m
                    interval = '15m' # Alterado de TRADING_CONFIG['interval'] para focar em 15m scalping
                    klines = await get_futures_klines(client, symbol, interval=interval, limit=100)
                except Exception as e:
                    # Ignora silenciosamente erros de símbolos inválidos no futuros (ex: SHIBUSDT -> 1000SHIBUSDT)
                    continue
                    
                if not klines or len(klines) < 20:
                    continue
                    
                import pandas as pd
                import datetime as dt_module
                
                closes = [float(k[4]) for k in klines]
                dates_rec = [dt_module.datetime.fromtimestamp(float(k[0])/1000).strftime('%H:%M') for k in klines]
                volumes_rec = [float(k[5]) for k in klines]
                
                df_rec = pd.DataFrame({'close': closes})
                sma20 = df_rec['close'].rolling(window=20).mean()
                std20 = df_rec['close'].rolling(window=20).std()
                bb_upper = (sma20 + 2 * std20).where(pd.notnull(sma20), None).tolist()
                bb_lower = (sma20 - 2 * std20).where(pd.notnull(sma20), None).tolist()
                ema200 = df_rec['close'].ewm(span=min(200, len(closes)), adjust=False).mean().where(pd.notnull(df_rec['close']), None).tolist()

                shared_futures_market_data['dates'] = dates_rec
                shared_futures_market_data['klines'] = [[float(k[1]), float(k[4]), float(k[3]), float(k[2])] for k in klines]
                shared_futures_market_data['bb_upper'] = bb_upper[-100:]
                shared_futures_market_data['bb_lower'] = bb_lower[-100:]
                shared_futures_market_data['ema200'] = ema200[-100:]
                shared_futures_market_data['volumes'] = volumes_rec
                
                bot_futures_status_data['target_asset'] = symbol

                cur_price = closes[-1]
                cur_open = float(klines[-1][1])
                rsi = calculate_rsi(closes)
                candle_patterns = check_candle_patterns(klines)
                macd_current, signal_line_current = calculate_macd(closes)
                
                # Análise de Volume Relativo (Pico recente)
                vol_sma = pd.Series(volumes_rec).rolling(10).mean().tolist()[-1] if len(volumes_rec) >= 10 else 0
                cur_vol = volumes_rec[-1]
                has_volume_spike = (cur_vol > (vol_sma * 1.5)) if vol_sma > 0 else True
                
                # Análise de Distância da EMA20
                ema20_val = pd.Series(closes).ewm(span=20, adjust=False).mean().iloc[-1]
                ema_dist_pct = ((cur_price - ema20_val) / ema20_val) * 100
                
                direction = None
                trigger_reason = ""
                
                # 1. Gemini AI Panic Scanner (Independente)
                from services.futures_gemini_news import evaluate_news_sentiment
                score, gemini_dir, reason = await evaluate_news_sentiment(symbol, log)
                if gemini_dir in ['LONG', 'SHORT'] and (score <= 25 or score >= 80):
                    direction = gemini_dir
                    trigger_reason = f"[GEMINI-AI] Notícia Extrema ({score}): {reason}"
                    
                # 1.5 Liquidation Hunter (Short Squeeze Detector)
                if not direction:
                    if bb_lower and len(bb_lower) > 0 and bb_lower[-1] and bb_upper and len(bb_upper) > 0 and bb_upper[-1]:
                        dist_lower_bb = ((cur_price - bb_lower[-1]) / bb_lower[-1]) * 100
                        dist_upper_bb = ((cur_price - bb_upper[-1]) / bb_upper[-1]) * 100
                        
                        if dist_lower_bb < -1.5 and has_volume_spike and rsi < 25:
                            direction = 'LONG'
                            trigger_reason = f"🩸 [LIQUIDATION HUNTER] Liquidação em massa detectada! Dist BB: {dist_lower_bb:.2f}% | RSI: {rsi:.1f}"
                        elif dist_upper_bb > 1.5 and has_volume_spike and rsi > 75:
                            direction = 'SHORT'
                            trigger_reason = f"🩸 [LIQUIDATION HUNTER] FOMO em massa detectado! Dist BB: +{dist_upper_bb:.2f}% | RSI: {rsi:.1f}"
                    
                # 2. 15m Bollinger Band Sniper (Reversão à Média Extrema)
                if not direction:
                    from core.futures_band_sniper import evaluate_band_sniper
                    sniper_dir, sniper_tp, sniper_sl = await evaluate_band_sniper(client, symbol, log)
                    if sniper_dir:
                        direction = sniper_dir
                        trigger_reason = f"🎯 [BAND-SNIPER 15M] Reversão à Média Detectada."
                        bot_futures_status_data['sniper_tp'] = sniper_tp
                        bot_futures_status_data['sniper_sl'] = sniper_sl
                    
                if not direction:
                    # 3. Lead-Lag Alpha + CVD (Independente)
                    from core.futures_lead_lag import evaluate_lead_lag
                    from core.futures_cvd_reader import evaluate_cvd
                    is_lagging, ll_direction = await evaluate_lead_lag(client, symbol)
                    if is_lagging and ll_direction:
                        cvd_delta, buy_ratio, cvd_direction = await evaluate_cvd(client, symbol)
                        if cvd_direction == ll_direction:
                            direction = ll_direction
                            trigger_reason = f"[LEAD-LAG] Alpha Impulse. Apoiado por [CVD] (Delta: {cvd_delta:.0f})"
                            
                if not direction:
                    # 4. Análise Técnica Padrão (BB + RSI) com filtro CVD
                    tech_dir = None
                    is_green_candle = cur_price > cur_open
                    is_red_candle = cur_price < cur_open
                    
                    from core.futures_cvd_reader import evaluate_cvd
                    cvd_delta, buy_ratio, cvd_direction = await evaluate_cvd(client, symbol)
                    
                    # Exaustão Extrema: Shortar o topo que rompeu a banda superior (independente se verde ou vermelho), ou Long no fundo.
                    if bb_upper and len(bb_upper) > 0 and bb_upper[-1] and cur_price >= bb_upper[-1]:
                        if rsi > 65:
                            tech_dir = 'SHORT'
                    elif bb_lower and len(bb_lower) > 0 and bb_lower[-1] and cur_price <= bb_lower[-1]:
                        if rsi < 35:
                            tech_dir = 'LONG'
                            
                    # Smart Relaxation: Se o RSI aponta exaustão e o Tape Reading (CVD) mostra força contrária confirmada
                    if not tech_dir:
                        if rsi < 40 and cvd_direction == 'LONG':
                            tech_dir = 'LONG'
                            log(f"🧠 [SMART RELAXATION] RSI em {rsi:.1f} com CVD comprador massivo. Validando LONG antecipado em {symbol}.")
                        elif rsi > 60 and cvd_direction == 'SHORT':
                            tech_dir = 'SHORT'
                            log(f"🧠 [SMART RELAXATION] RSI em {rsi:.1f} com CVD vendedor massivo. Validando SHORT antecipado em {symbol}.")
                            
                    if tech_dir:
                        # Aborta se CVD apontar forte para a direção oposta
                        if tech_dir == 'LONG' and cvd_direction == 'SHORT':
                            log(f"⚠️ [CVD] Abortando LONG em {symbol} devido a agressão vendedora massiva (Delta: {cvd_delta:.0f}).")
                        elif tech_dir == 'SHORT' and cvd_direction == 'LONG':
                            log(f"⚠️ [CVD] Abortando SHORT em {symbol} devido a agressão compradora massiva (Delta: {cvd_delta:.0f}).")
                        else:
                            direction = tech_dir
                            trigger_reason = f"[TÉCNICO] Validação Direcional. Filtro [CVD] Confirmou (Delta: {cvd_delta:.0f})"
                            
                if not direction:
                    # 5. Price Action Reversão (Candle + MACD)
                    has_bullish_candle = any(p in candle_patterns for p in ["Hammer", "Bullish Engulfing", "Piercing Line", "Morning Star", "Bullish Kicker"])
                    has_bearish_candle = any(p in candle_patterns for p in ["Shooting Star", "Bearish Engulfing", "Dark Cloud Cover", "Evening Star", "Bearish Kicker", "Gravestone Doji"])
                    
                    if has_bullish_candle and macd_current > signal_line_current:
                        direction = 'LONG'
                        trigger_reason = f"🕯️ [PRICE ACTION] Padrão Altista ({candle_patterns[0]}) com MACD cruzando."
                    elif has_bearish_candle and macd_current < signal_line_current:
                        direction = 'SHORT'
                        trigger_reason = f"🕯️ [PRICE ACTION] Padrão Baixista ({candle_patterns[0]}) com MACD cruzando."
                            
                # --- FILTROS FINAIS (Mercado e Volume) ---
                if direction:
                    # Escudo de Price Action (Filtro Defensivo)
                    has_bullish_defense = any(p in candle_patterns for p in ["Hammer", "Bullish Engulfing", "Piercing Line", "Bullish Kicker"])
                    has_bearish_defense = any(p in candle_patterns for p in ["Shooting Star", "Bearish Engulfing", "Dark Cloud Cover", "Bearish Kicker"])
                    
                    if direction == 'LONG' and has_bearish_defense and '[PRICE ACTION]' not in trigger_reason:
                        log(f"🛡️ [CANDLE SHIELD] Bloqueando LONG em {symbol} devido à vela forte de rejeição ({candle_patterns[0]}).")
                        direction = None
                        
                    elif direction == 'SHORT' and has_bullish_defense and '[PRICE ACTION]' not in trigger_reason:
                        log(f"🛡️ [CANDLE SHIELD] Bloqueando SHORT em {symbol} devido à vela forte de rejeição ({candle_patterns[0]}).")
                        direction = None
                        
                    elif not has_volume_spike and '[GEMINI-AI]' not in trigger_reason:
                        log(f"⚠️ [VOLUME] {symbol} sem liquidez/volume suficiente para entrada segura. Ignorando.")
                        direction = None
                        
                    elif direction == 'LONG' and ema_dist_pct < 0.8 and '[GEMINI-AI]' not in trigger_reason and '[BAND-SNIPER 15M]' not in trigger_reason:
                        log(f"🛡️ [EMA DIST] Bloqueando LONG em {symbol} pois preço não rompeu a EMA20 com força (Distância: {ema_dist_pct:.2f}%).")
                        direction = None
                        
                    elif direction == 'SHORT' and ema_dist_pct > -0.8 and '[GEMINI-AI]' not in trigger_reason and '[BAND-SNIPER 15M]' not in trigger_reason:
                        log(f"🛡️ [EMA DIST] Bloqueando SHORT em {symbol} pois preço não rompeu a EMA20 com força (Distância: {ema_dist_pct:.2f}%).")
                        direction = None
                        
                    elif direction == 'LONG' and btc_trend == 'BEAR' and symbol != 'BTCUSDT':
                        log(f"🛡️ [REGIME] Bloqueando LONG em {symbol} pois o BTC está em tendência de BAIXA (RSI: {btc_rsi:.1f}).")
                        direction = None
                        
                    elif direction == 'SHORT' and btc_trend == 'BULL' and symbol != 'BTCUSDT':
                        log(f"🛡️ [REGIME] Bloqueando SHORT em {symbol} pois o BTC está em tendência de ALTA (RSI: {btc_rsi:.1f}).")
                        direction = None
                # ----------------------------------------
                # ----------------------------------------

                # --- ALINHAMENTO MULTI-TIMEFRAME & WHALE TRACKER (Lazy Load) ---
                if direction and '[BAND-SNIPER 15M]' not in trigger_reason:
                    # 1. MTF 1H EMA20
                    try:
                        klines_1h = await get_futures_klines(client, symbol, interval='1h', limit=50)
                        if klines_1h and len(klines_1h) >= 20:
                            import pandas as pd
                            closes_1h = [float(k[4]) for k in klines_1h]
                            ema20_1h = pd.Series(closes_1h).ewm(span=20, adjust=False).mean().tolist()[-1]
                            price_1h = closes_1h[-1]
                            
                            if direction == 'LONG' and price_1h < ema20_1h:
                                log(f"🛡️ [MTF] Bloqueando LONG em {symbol}. Preço no 1H (${price_1h:.4f}) está abaixo da EMA20 (${ema20_1h:.4f}).")
                                direction = None
                            elif direction == 'SHORT' and price_1h > ema20_1h:
                                log(f"🛡️ [MTF] Bloqueando SHORT em {symbol}. Preço no 1H (${price_1h:.4f}) está acima da EMA20 (${ema20_1h:.4f}).")
                                direction = None
                    except Exception as e:
                        log(f"⚠️ Erro ao checar MTF de {symbol}: {e}")
                        
                    # 2. Whale Long/Short Ratio
                    if direction:
                        whale_ratio = await get_futures_whale_ratio(client, symbol, '15m')
                        if direction == 'LONG' and whale_ratio < 0.85:
                            log(f"🐋 [WHALE TRACKER] Bloqueando LONG em {symbol}. Top Traders estão massivamente VENDIDOS (Ratio: {whale_ratio:.2f}).")
                            direction = None
                        elif direction == 'SHORT' and whale_ratio > 1.15:
                            log(f"🐋 [WHALE TRACKER] Bloqueando SHORT em {symbol}. Top Traders estão massivamente COMPRADOS (Ratio: {whale_ratio:.2f}).")
                            direction = None

                if direction:
                    log(f"🚨 [FUTUROS] Oportunidade {direction} detectada em {symbol} (Gatilho: {trigger_reason})")
                    log(f"🎯 [SNIPER MODE] Iniciando observação tática por até 3 minutos para exaustão...")
                    
                    import time
                    stalk_start = time.time()
                    stalk_duration = 180
                    price_history = []
                    stalk_success = False
                    
                    while time.time() - stalk_start < stalk_duration:
                        if not bot_futures_running: break
                        try:
                            ticker = await client.futures_symbol_ticker(symbol=symbol)
                            realtime_price = float(ticker['price'])
                            price_history.append(realtime_price)
                            
                            if len(price_history) > 5:
                                price_history.pop(0)
                                
                            if len(price_history) == 5:
                                p_oldest = price_history[0]
                                p_newest = price_history[-1]
                                
                                if direction == 'LONG':
                                    if realtime_price <= cur_price and p_newest > p_oldest:
                                        log(f"🔥 [SNIPER] Exaustão detectada! Desconto obtido: {cur_price} -> {realtime_price}. FOGO!")
                                        cur_price = realtime_price
                                        stalk_success = True
                                        break
                                    elif realtime_price > cur_price * 1.002: 
                                        log(f"🔥 [SNIPER] Preço subindo rápido ({realtime_price}). Entrando a mercado!")
                                        cur_price = realtime_price
                                        stalk_success = True
                                        break
                                elif direction == 'SHORT':
                                    if realtime_price >= cur_price and p_newest < p_oldest:
                                        log(f"🔥 [SNIPER] Exaustão detectada! Ágio obtido: {cur_price} -> {realtime_price}. FOGO!")
                                        cur_price = realtime_price
                                        stalk_success = True
                                        break
                                    elif realtime_price < cur_price * 0.998:
                                        log(f"🔥 [SNIPER] Preço caindo rápido ({realtime_price}). Entrando a mercado!")
                                        cur_price = realtime_price
                                        stalk_success = True
                                        break
                            await asyncio.sleep(2)
                        except Exception as e:
                            log(f"⚠️ Erro no Modo Sniper: {e}")
                            break
                            
                    if not stalk_success:
                        log(f"🛑 [SNIPER] Alvo {symbol} perdido. A exaustão não confirmou. Trade abortado.")
                        direction = None

                if direction:
                    # Re-avalia o saldo antes de entrar, pois operações anteriores no mesmo ciclo podem ter consumido a margem
                    current_balance = await get_futures_usdt_balance(client)
                    # 1. Dimensionamento Fixo (Saldo / 3 - 5% de segurança)
                    margin_usdt = (current_balance / 3) * 0.95
                    log(f"🏆 \033[1;36mFixed Slot Sizing\033[0m: Margem de trade alocada em \033[1;32m${margin_usdt:.2f} USDT\033[0m (1/3 do saldo Futuros).")
                    
                    if current_balance < margin_usdt:
                        log(f"⚠️ Saldo atual ({current_balance:.2f}) menor que margem exigida ({margin_usdt:.2f}). Pausando scanner...")
                        break
                        
                    initial_leverage = 20
                    
                    # 2. Definição do TP/SL (Dinâmico)
                    if '[BAND-SNIPER 15M]' in trigger_reason:
                        tp_price = bot_futures_status_data.pop('sniper_tp')
                        sl_price = bot_futures_status_data.pop('sniper_sl')
                        
                        # Trava de segurança: Garante que o TP não ultrapasse 8% de ROE (0.40% preço)
                        if direction == 'LONG':
                            tp_price = min(tp_price, cur_price * 1.0040)
                        else:
                            tp_price = max(tp_price, cur_price * 0.9960)
                    else:
                        atr_val, atr_pct = calculate_atr(klines, period=14)
                        if atr_val == 0: atr_val = cur_price * 0.015
                        
                        if '[GEMINI-AI]' in trigger_reason:
                            tp_dist = atr_val * 0.5
                            sl_dist = atr_val * 0.3
                        else:
                            tp_dist = atr_val * 1.0  # Alvo 1x ATR
                            sl_dist = atr_val * 1.5  # Stop Seguro 1.5x ATR
                            
                        if direction == 'LONG':
                            tp_price = cur_price + tp_dist
                            sl_price = cur_price - sl_dist
                        else:
                            tp_price = cur_price - tp_dist
                            sl_price = cur_price + sl_dist
                    
                    # 3. Gerenciamento de Risco (Liquidation Buffer)
                    from core.futures_risk_manager import validate_trade_safety
                    safe_leverage = await validate_trade_safety(symbol, cur_price, sl_price, initial_leverage, direction, log)
                    
                    if safe_leverage == 0:
                        continue # Rejeitado
                        
                    leverage = safe_leverage
                    notional = margin_usdt * leverage
                    
                    # Dinamicamente buscar a precisão do ativo
                    info = symbols_info.get(symbol, {})
                    qty_precision = 3
                    price_precision = 4
                    if info:
                        for f in info.get('filters', []):
                            if f['filterType'] == 'LOT_SIZE':
                                qty_precision = get_precision(float(f['stepSize']))
                            if f['filterType'] == 'PRICE_FILTER':
                                price_precision = get_precision(float(f['tickSize']))
                                
                    from decimal import Decimal, ROUND_DOWN
                    step_size_str = "0.001"
                    if info:
                        for f in info.get('filters', []):
                            if f['filterType'] == 'LOT_SIZE':
                                step_size_str = f['stepSize']
                    qty_dec = Decimal(str(notional / cur_price))
                    step_dec = Decimal(step_size_str)
                    quantized_qty = (qty_dec / step_dec).quantize(Decimal('1'), rounding=ROUND_DOWN) * step_dec
                    qty = float(quantized_qty)
                    if qty <= 0:
                        log(f"⚠️ Quantidade calculada ({qty_dec}) menor que lote mínimo ({step_size_str}) em {symbol}. Ignorando.")
                        continue
                    
                    try:
                        # Setup alavancagem Isolada SOMENTE quando for entrar no trade (economia de API Rate Limit)
                        if not await setup_futures_margin(client, symbol, leverage=leverage, margin_type='ISOLATED'):
                            log(f"⚠️ Falha ao configurar margem/alavancagem em {symbol}. Cancelando entrada.")
                            continue

                        from core.futures_order_manager import place_futures_trade_with_protection
                        side_entry = 'BUY' if direction == 'LONG' else 'SELL'
                        
                        # Usando a precisão real da exchange
                        tp_price = round(tp_price, price_precision)
                        sl_price = round(sl_price, price_precision)
                        
                        entry_order, tp_order, sl_order, entry_price_executed = await place_futures_trade_with_protection(
                            client, symbol, side_entry, qty, tp_price, sl_price, leverage, log
                        )
                        
                        if not entry_order:
                            continue
                            
                        await futures_state.add(symbol, {
                            'entry': entry_price_executed, 'tp': tp_price, 'sl': sl_price, 'direction': direction, 'qty': qty
                        })
                        bot_futures_status_data['active_symbols'] = list((await futures_state.get_all()).keys())
                        
                        from config.settings import TELEGRAM_CONFIG
                        if TELEGRAM_CONFIG.get('bot_token') and TELEGRAM_CONFIG.get('chat_id'):
                            asyncio.create_task(send_telegram_message(
                                TELEGRAM_CONFIG['bot_token'], TELEGRAM_CONFIG['chat_id'],
                                f"<b>✅ [FUTUROS] Posição {direction} Aberta!</b>\n\n"
                                f"🪙 <b>Ativo:</b> {symbol}\n"
                                f"💡 <b>Motivo:</b> {trigger_reason}\n"
                                f"💰 <b>Entrada:</b> ${entry_price_executed:.4f}\n"
                                f"🎯 <b>Take Profit:</b> ${tp_price}\n"
                                f"🛑 <b>Stop Loss:</b> ${sl_price}\n"
                                f"⚡ <b>Alavancagem:</b> {leverage}x"
                            ))
                        
                    except Exception as e:
                        log(f"❌ Erro ao abrir posição {direction} em {symbol}: {e}")
                        
            await asyncio.sleep(5)
            
        except Exception as e:
            log(f"⚠️ Erro no Motor de Futuros: {e}")
            await asyncio.sleep(10)

async def panic_sell_futures_position(client, symbol, qty=0, log=print):
    """Fecha imediatamente a posição de futuros (Panic Sell) e limpa ordens condicionais."""
    active_positions = await futures_state.get_all()
    if symbol not in active_positions:
        return False, "Nenhuma posição aberta neste par."
    
    pos_data = await futures_state.get(symbol)
    direction = pos_data['direction']
    close_side = 'SELL' if direction == 'LONG' else 'BUY'
    
    try:
        # Tenta pegar ordens em aberto para cancelar
        open_orders = await client.futures_get_open_orders(symbol=symbol)
        for order in open_orders:
            try:
                await client.futures_cancel_order(symbol=symbol, orderId=order['orderId'])
            except: pass
            
        # O qty não é armazenado localmente para simplificar no dicionário
        # precisaremos buscar a quantidade exata da posição aberta se qty=0
        if qty == 0:
            positions = await client.futures_position_information(symbol=symbol)
            for p in positions:
                if float(p['positionAmt']) != 0:
                    qty = abs(float(p['positionAmt']))
                    break
        
        if qty > 0:
            await client.futures_create_order(symbol=symbol, side=close_side, type='MARKET', quantity=qty, reduceOnly='true')
            
        await futures_state.remove(symbol)
        bot_futures_status_data['active_symbols'] = list((await futures_state.get_all()).keys())
        log(f"🔥 PANIC SELL FUTUROS: {symbol} liquidado a mercado!")
        return True, f"Posição de Futuros em {symbol} liquidada a mercado."
    except Exception as e:
        log(f"Erro no Panic Sell Futuros: {e}")
        return False, str(e)
