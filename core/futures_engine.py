import asyncio
import time
from config.settings import TELEGRAM_CONFIG, TIMEZONE, TOP_10_FUTURES_SYMBOLS, TRADING_CONFIG
from core.futures_state import futures_state
from services.binance_client import (
    setup_futures_margin, place_futures_order, place_futures_conditional_order,
    get_futures_usdt_balance, get_futures_usdt_total_balance, get_futures_klines, get_futures_whale_ratio
)
from core.decision import get_precision
from core.indicators import (
    calculate_rsi, check_candle_patterns, calculate_macd, calculate_atr,
    calculate_bollinger_bandwidth, calculate_orderbook_imbalance
)
from core.futures_order_manager import monitor_futures_lifecycle
from services.telegram_notifier import send_telegram_message

bot_futures_running = False
bot_futures_status_data = {
    "price": 0, "symbol": "", "action": "Aguardando...", "target_asset": "BTCUSDT",
    "active_symbols": [], "active_positions": futures_state.get_all_sync()
}

shared_futures_market_data = {
    'dates': [], 'klines': [], 'bb_upper': [], 'bb_lower': [], 'ema200': [], 'volumes': []
}

import time
_log_throttle = {}

def log_throttled(msg, key, log_fn, cooldown=300):
    now = time.time()
    if now - _log_throttle.get(key, 0) > cooldown:
        log_fn(msg)
        _log_throttle[key] = now

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
    
    daily_starting_balance = 0.0
    daily_balance_day = None
    circuit_breaker_active_until = 0

    futures_bg_tasks = []
    try:
        futures_bg_tasks.append(asyncio.create_task(run_futures_user_stream(client, db, log)))
        futures_bg_tasks.append(asyncio.create_task(run_fallback_position_monitor(client, db, log)))
        futures_bg_tasks.append(asyncio.create_task(run_trailing_lock_monitor(client, log)))
        
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
                        # Busca step_size no symbols_info
                        s_info = symbols_info.get(rec_symbol, {})
                        rec_step_str = "0.001"
                        if s_info:
                            for f in s_info.get('filters', []):
                                if f['filterType'] == 'LOT_SIZE':
                                    rec_step_str = f['stepSize']

                        await futures_state.add(rec_symbol, {
                            'entry': entry_price, 'tp': tp_price, 'sl': sl_price, 'direction': direction,
                            'qty': qty, 'step_size': rec_step_str
                        })
                        bot_futures_status_data['active_symbols'] = list((await futures_state.get_all()).keys())
                        bot_futures_status_data['target_asset'] = rec_symbol
                    
                        try:
                            import pandas as pd
                            import datetime as dt_module
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
                        futures_bg_tasks.append(asyncio.create_task(monitor_futures_lifecycle(
                            client, bsm, rec_symbol, direction, entry_price, qty,
                            tp_order.get('algoId'), sl_order.get('algoId'), tp_price, sl_price, db,
                            bot_futures_status_data, log, status
                        )))
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
                # 0. Circuit Breaker Diário (-30.0% Max Daily Drawdown na Testnet)
                import datetime as dt_module
                now_brt = dt_module.datetime.now(TIMEZONE)
                current_day = now_brt.strftime("%Y-%m-%d")

                if daily_balance_day != current_day:
                    daily_starting_balance = await get_futures_usdt_total_balance(client)
                    daily_balance_day = current_day
                    circuit_breaker_active_until = 0
                    log(f"📅 [CIRCUIT-BREAKER] Saldo Base do Dia ({current_day}) fixado em: ${daily_starting_balance:.2f} USDT")

                if time.time() < circuit_breaker_active_until:
                    remaining_pause = int((circuit_breaker_active_until - time.time()) / 60)
                    status(f"🛑 [CIRCUIT-BREAKER] Pausa de segurança ativa por mais {remaining_pause} min devido a Drawdown Diário.")
                    await asyncio.sleep(60)
                    continue

                cur_total_bal = await get_futures_usdt_total_balance(client)
                if daily_starting_balance > 0:
                    daily_dd_pct = ((cur_total_bal - daily_starting_balance) / daily_starting_balance) * 100
                    if daily_dd_pct <= -30.0:
                        circuit_breaker_active_until = time.time() + (6 * 3600)  # Pausa de 6 horas
                        log(f"🚨 [CIRCUIT-BREAKER ATIVADO] Prejuízo diário de {daily_dd_pct:.2f}% atingiu limite de -30.0%! Pausando novas entradas por 6 horas.")
                        if TELEGRAM_CONFIG.get('bot_token') and TELEGRAM_CONFIG.get('chat_id'):
                            asyncio.create_task(send_telegram_message(
                                TELEGRAM_CONFIG['bot_token'], TELEGRAM_CONFIG['chat_id'],
                                f"🚨 <b>CIRCUIT BREAKER DIÁRIO ATIVADO!</b>\n\n"
                                f"📉 <b>Drawdown Diário:</b> {daily_dd_pct:.2f}%\n"
                                f"💰 <b>Saldo Inicial:</b> ${daily_starting_balance:.2f}\n"
                                f"💰 <b>Saldo Atual:</b> ${cur_total_bal:.2f}\n\n"
                                f"🛡️ <i>Novas operações suspensas por 6 horas para preservar o patrimônio.</i>"
                            ))
                        await asyncio.sleep(60)
                        continue

                for symbol in symbols_to_scan:
                    if not bot_futures_running: break
                    
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
                
                    # Cálculo do Histograma MACD para detectar exaustão da força direcional
                    try:
                        _exp1 = pd.Series(closes).ewm(span=12, adjust=False).mean()
                        _exp2 = pd.Series(closes).ewm(span=26, adjust=False).mean()
                        _macd_s = _exp1 - _exp2
                        _sig_s = _macd_s.ewm(span=9, adjust=False).mean()
                        _hist_s = _macd_s - _sig_s
                        macd_hist_curr = _hist_s.iloc[-1]
                        macd_hist_prev = _hist_s.iloc[-2]
                    except Exception:
                        macd_hist_curr, macd_hist_prev = 0.0, 0.0
                
                    # Análise de Volume Relativo (Pico recente)
                    vol_sma = pd.Series(volumes_rec).rolling(10).mean().tolist()[-1] if len(volumes_rec) >= 10 else 0
                    cur_vol = volumes_rec[-1]
                    has_volume_spike = (cur_vol > (vol_sma * 1.3)) if vol_sma > 0 else True
                
                    # Análise de Distância da EMA20
                    ema20_val = pd.Series(closes).ewm(span=20, adjust=False).mean().iloc[-1]
                    ema_dist_pct = ((cur_price - ema20_val) / ema20_val) * 100
                
                    direction = None
                    trigger_reason = ""
                
                    # 1. Gemini AI Panic Scanner (Independente)
                    from services.futures_gemini_news import evaluate_news_sentiment
                    if has_volume_spike:
                        score, gemini_dir, reason = await evaluate_news_sentiment(symbol, log)
                        if gemini_dir in ['LONG', 'SHORT'] and (score <= 25 or score >= 80):
                            direction = gemini_dir
                            trigger_reason = f"[GEMINI-AI] Notícia Extrema ({score}): {reason}"
                    
                    # 1.5 Liquidation Hunter (Short Squeeze Detector)
                    if not direction:
                        if bb_lower and len(bb_lower) > 0 and bb_lower[-1] and bb_upper and len(bb_upper) > 0 and bb_upper[-1]:
                            dist_lower_bb = ((cur_price - bb_lower[-1]) / bb_lower[-1]) * 100
                            dist_upper_bb = ((cur_price - bb_upper[-1]) / bb_upper[-1]) * 100
                            
                            # Filtro Squeeze: Verifica se o mercado está comprimido demais (< 0.8% Bandwidth) sem volume
                            bb_width = calculate_bollinger_bandwidth(bb_upper[-1], bb_lower[-1], sma20.iloc[-1] if hasattr(sma20, 'iloc') else cur_price)
                            if bb_width < 0.8 and not has_volume_spike:
                                log_throttled(f"💤 [SQUEEZE] {symbol} em compressão extrema ({bb_width:.2f}% width) sem volume. Aguardando rompimento.", f"sqz_{symbol}", log, 3600)
                                continue
                        
                            if dist_lower_bb < -1.5 and has_volume_spike and rsi < 25:
                                direction = 'LONG'
                                trigger_reason = f"📉 [BAND-SNIPER 15M] Perfuração violenta da Banda Inferior ({dist_lower_bb:.2f}%) com RSI extremo ({rsi:.1f})"
                            elif dist_upper_bb > 1.5 and has_volume_spike and rsi > 75:
                                direction = 'SHORT'
                                trigger_reason = f"📈 [BAND-SNIPER 15M] Fura-Teto violento da Banda Superior ({dist_upper_bb:.2f}%) com RSI extremo ({rsi:.1f})"
                            else:
                                if not has_volume_spike:
                                    log_throttled(f"🧠 [SMART RELAXATION] RSI em {rsi:.1f} com CVD vendedor massivo. Validando SHORT antecipado em {symbol}.", f"smart_relax_{symbol}", log, 7200)
                                    pass
                    
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
                        cvd_delta, buy_ratio, cvd_direction = 0, 0.5, None
                    
                        # Lazy Evaluation: Só busca o CVD (que consome muito peso da API) se a moeda tiver chance de dar trade (RSI extremo)
                        if rsi < 35 or rsi > 65:
                            cvd_delta, buy_ratio, cvd_direction = await evaluate_cvd(client, symbol)
                    
                        # Exaustão Extrema: Shortar o topo que rompeu a banda superior (independente se verde ou vermelho), ou Long no fundo.
                        if bb_upper and len(bb_upper) > 0 and bb_upper[-1] and cur_price >= bb_upper[-1]:
                            if rsi > 70:
                                tech_dir = 'SHORT'
                        elif bb_lower and len(bb_lower) > 0 and bb_lower[-1] and cur_price <= bb_lower[-1]:
                            if rsi < 30:
                                tech_dir = 'LONG'
                            
                        # Smart Relaxation: Se o RSI aponta exaustão e o Tape Reading (CVD) mostra força contrária confirmada
                        if not tech_dir:
                            if rsi < 35 and cvd_direction == 'LONG':
                                tech_dir = 'LONG'
                                log_throttled(f"🧠 [SMART RELAXATION] RSI em {rsi:.1f} com CVD comprador massivo. Validando LONG antecipado em {symbol}.", f"smart_relax_{symbol}", log, 7200)
                            elif rsi > 65 and cvd_direction == 'SHORT':
                                tech_dir = 'SHORT'
                                log_throttled(f"🧠 [SMART RELAXATION] RSI em {rsi:.1f} com CVD vendedor massivo. Validando SHORT antecipado em {symbol}.", f"smart_relax_{symbol}", log, 7200)
                            
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
                        
                        # Filtro Anti-Foguete / Anti-Faca Caindo (Impede entrar contra uma barra de força absoluta)
                        is_green_candle = cur_price > cur_open
                        is_red_candle = cur_price < cur_open
                        candle_variation = (abs(cur_price - cur_open) / cur_open) * 100
                    
                        if direction == 'SHORT' and is_green_candle and candle_variation > 0.35:
                            if '[PRICE ACTION]' not in trigger_reason:
                                log_throttled(f"🛡️ [ANTI-FOGUETE] Bloqueando SHORT em {symbol} pois a vela de alta é muito forte ({candle_variation:.2f}%). Aguardando exaustão!", f"foguete_{symbol}", log, 600)
                                direction = None
                            
                        elif direction == 'LONG' and is_red_candle and candle_variation > 0.35:
                            if '[PRICE ACTION]' not in trigger_reason:
                                log_throttled(f"🛡️ [ANTI-FACA] Bloqueando LONG em {symbol} pois a vela de baixa é muito forte ({candle_variation:.2f}%). Aguardando exaustão!", f"faca_{symbol}", log, 600)
                                direction = None
                        
                        elif direction == 'LONG' and macd_hist_curr > 0 and macd_hist_curr < macd_hist_prev:
                            if '[PRICE ACTION]' not in trigger_reason and '[BAND-SNIPER' not in trigger_reason:
                                log_throttled(f"🛡️ [MACD EXAUSTÃO] Bloqueando LONG em {symbol} pois a força compradora no MACD já está caindo.", f"macd_long_{symbol}", log, 600)
                                direction = None
                            
                        elif direction == 'SHORT' and macd_hist_curr < 0 and macd_hist_curr > macd_hist_prev:
                            if '[PRICE ACTION]' not in trigger_reason and '[BAND-SNIPER' not in trigger_reason:
                                log_throttled(f"🛡️ [MACD EXAUSTÃO] Bloqueando SHORT em {symbol} pois a força vendedora no MACD já está caindo.", f"macd_ex_{symbol}", log, 600)
                                direction = None
                            
                        elif direction == 'SHORT' and rsi > 78:
                            log_throttled(f"🛡️ [MOMENTUM EXTREMO] Bloqueando SHORT em {symbol} pois o RSI está parabólico (RSI: {rsi:.1f}).", f"mom_short_{symbol}", log, 600)
                            direction = None
                        
                        elif direction == 'LONG' and rsi < 22:
                            log_throttled(f"🛡️ [MOMENTUM EXTREMO] Bloqueando LONG em {symbol} pois o ativo está em queda livre (RSI: {rsi:.1f}).", f"mom_long_{symbol}", log, 600)
                            direction = None
                        
                        elif not has_volume_spike and '[GEMINI-AI]' not in trigger_reason:
                            log_throttled(f"⚠️ [VOLUME] {symbol} sem liquidez/volume suficiente para entrada segura. Ignorando.", f"vol_{symbol}", log, 7200)
                            direction = None
                        
                        elif direction == 'LONG' and ema_dist_pct < 1.0 and '[GEMINI-AI]' not in trigger_reason and '[BAND-SNIPER 15M]' not in trigger_reason:
                            log_throttled(f"🛡️ [EMA DIST] Bloqueando LONG em {symbol} pois preço não rompeu a EMA20 com força (Distância: {ema_dist_pct:.2f}%). Exigido > 1.0%", f"ema_{symbol}", log, 600)
                            direction = None
                        
                        elif direction == 'SHORT' and ema_dist_pct > -1.0 and '[GEMINI-AI]' not in trigger_reason and '[BAND-SNIPER 15M]' not in trigger_reason:
                            log_throttled(f"🛡️ [EMA DIST] Bloqueando SHORT em {symbol} pois preço não rompeu a EMA20 com força (Distância: {ema_dist_pct:.2f}%). Exigido < -1.0%", f"ema_{symbol}", log, 600)
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
                    if direction:
                        # 1. MTF 1H EMA20
                        try:
                            klines_1h = await get_futures_klines(client, symbol, interval='1h', limit=50)
                            if klines_1h and len(klines_1h) >= 20:
                                import pandas as pd
                                closes_1h = [float(k[4]) for k in klines_1h]
                                # Avalia com base no último candle FECHADO ([-2]) para evitar fakeouts
                                ema20_1h = pd.Series(closes_1h[:-1]).ewm(span=20, adjust=False).mean().tolist()[-1]
                                price_1h = closes_1h[-2]
                            
                                if direction == 'LONG' and price_1h < ema20_1h:
                                    log(f"🛡️ [MTF] Bloqueando LONG em {symbol}. Preço no 1H (${price_1h:.4f}) está abaixo da EMA20 (${ema20_1h:.4f}).")
                                    direction = None
                                elif direction == 'SHORT' and price_1h > ema20_1h:
                                    log(f"🛡️ [MTF] Bloqueando SHORT em {symbol}. Preço no 1H (${price_1h:.4f}) está acima da EMA20 (${ema20_1h:.4f}).")
                                    direction = None
                        except Exception as e:
                            log(f"⚠️ Erro ao checar MTF de {symbol}: {e}")
                        
                        # 2. Smart Money & Whale Tracker (Evolução v2)
                        if direction:
                            from core.futures_smart_money import evaluate_smart_money
                            sm_dir, top_ratio, taker_ratio = await evaluate_smart_money(client, symbol, '15m', log)
                        
                            # Bloqueio Defensivo: Se o Smart Money (Taker + Top Traders) for totalmente contra nós
                            if direction == 'LONG' and sm_dir == 'SHORT':
                                log(f"🐋 [SMART MONEY] Bloqueando LONG em {symbol}. Baleias VENDENDO pesado (Top: {top_ratio:.2f}, Taker: {taker_ratio:.2f}).")
                                direction = None
                            elif direction == 'SHORT' and sm_dir == 'LONG':
                                log(f"🐋 [SMART MONEY] Bloqueando SHORT em {symbol}. Baleias COMPRANDO pesado (Top: {top_ratio:.2f}, Taker: {taker_ratio:.2f}).")
                                direction = None
                            elif sm_dir == direction:
                                log(f"🐋 [SMART MONEY] Confluência ATIVA! Baleias e sistema apontando para {direction}. (Top: {top_ratio:.2f})")

                        # 3. Orderbook Imbalance & Wall Detection em Futuros
                        if direction:
                            try:
                                ob_fut = await client.futures_order_book(symbol=symbol, limit=20)
                                ob_ratio, total_bid_vol, total_ask_vol, has_buy_wall, ob_msg = calculate_orderbook_imbalance(ob_fut)
                                
                                if direction == 'LONG' and ob_ratio < 0.6:
                                    log(f"🛡️ [ORDERBOOK] Bloqueando LONG em {symbol}. Pressão vendedora massiva no Book (Bids/Asks: {ob_ratio:.2f}x).")
                                    direction = None
                                elif direction == 'SHORT' and ob_ratio > 1.8:
                                    log(f"🛡️ [ORDERBOOK] Bloqueando SHORT em {symbol}. Muro de compra pesado no Book (Bids/Asks: {ob_ratio:.2f}x).")
                                    direction = None
                                else:
                                    log(f"🌊 [ORDERBOOK] Fluxo confirmado para {direction} em {symbol} (Razão Bids/Asks: {ob_ratio:.2f}x)")
                            except Exception as ob_err:
                                log(f"⚠️ Aviso ao verificar Orderbook de Futuros em {symbol}: {ob_err}")

                    if direction:
                        log(f"🚨 [FUTUROS] Oportunidade {direction} detectada em {symbol} (Gatilho: {trigger_reason})")
                        log(f"🎯 [SNIPER MODE] Iniciando observação tática por até 3 minutos para exaustão...")
                    
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
                        # Re-avalia o saldo antes de entrar
                        available_balance = await get_futures_usdt_balance(client)
                        total_balance = await get_futures_usdt_total_balance(client)
                    
                        # 1. Dimensionamento Dinâmico por Risco Escalonado (Fase 2)
                        # Adapta o risco de acordo com o tamanho da banca (Bancas menores precisam de % maior para atingir o mínimo nocional)
                        if total_balance <= 200:
                            risk_pct = 0.08  # 8% para bancas muito pequenas
                        elif total_balance <= 1000:
                            risk_pct = 0.05  # 5% para bancas pequenas
                        elif total_balance <= 3000:
                            risk_pct = 0.03  # 3% para bancas médias
                        else:
                            risk_pct = 0.02  # 2% Institucional para bancas grandes (> $3000)
                            
                        risk_amount = total_balance * risk_pct
                        log(f"🏆 \033[1;36mDynamic Risk Sizing\033[0m: Risco aceito de \033[1;32m${risk_amount:.2f} USDT\033[0m ({risk_pct*100:.1f}% da banca de ${total_balance:.2f}).")
                        

                    
                        atr_val, atr_pct = calculate_atr(klines, period=14)
                        if atr_val == 0: atr_val = cur_price * 0.015
                        if atr_pct == 0: atr_pct = 0.015

                        # 2. Definição do TP/SL (Dinâmico Hedge Fund)
                        # 2. Definição do TP/SL (Otimizado por Backtest Quantitativo nos últimos 60 dias)
                        if '[BAND-SNIPER 15M]' in trigger_reason:
                            tp_price = bot_futures_status_data.pop('sniper_tp')
                            sl_price = bot_futures_status_data.pop('sniper_sl')
                        else:
                            if '[GEMINI-AI]' in trigger_reason:
                                tp_dist = atr_val * 1.5
                                sl_dist = atr_val * 1.0
                            else:
                                tp_dist = atr_val * 2.5  # Alvo Quant 2.5x ATR (Relação R:R Assimétrica Positiva)
                                sl_dist = atr_val * 1.5  # Stop Institucional 1.5x ATR
                            
                            if direction == 'LONG':
                                tp_price = cur_price + tp_dist
                                sl_price = cur_price - sl_dist
                            else:
                                tp_price = cur_price - tp_dist
                                sl_price = cur_price + sl_dist
                    
                        # Calcula a distância percentual do Stop Loss
                        sl_pct_dist = abs(cur_price - sl_price) / cur_price
                        
                        # Alavancagem Dinâmica Inteligente: Travado em 20x max para garantir margem de manobra e evitar liquidação por ruído
                        raw_leverage = 1.0 / (sl_pct_dist * 2.0) if sl_pct_dist > 0 else 1.0
                        initial_leverage = max(3, min(20, int(raw_leverage)))
                        
                        log(f"⚙️ \033[1;36mDynamic Leverage\033[0m: Stop a {sl_pct_dist*100:.2f}% | Alavancagem Institucional: \033[1;33m{initial_leverage}x\033[0m")
                        
                        # 3. Gerenciamento de Risco (Liquidation Buffer)
                        from core.futures_risk_manager import validate_trade_safety
                        safe_leverage = await validate_trade_safety(symbol, cur_price, sl_price, initial_leverage, direction, log)
                    
                        if safe_leverage == 0:
                            continue # Rejeitado
                        
                        leverage = safe_leverage
                        # Limites Absolutos Otimizados (Removido hardcap de 5.5% e 10% para deixar o ATR fluir)
                        # A matemática provou que cortar os lucros e as folgas de stop cedo demais estraga a curva de capital.
                        
                        # Calcula a distância do SL e a Notional necessária para perder apenas o risk_amount
                        sl_price_diff = abs(cur_price - sl_price)
                        if sl_price_diff == 0:
                            continue
                            
                        # Quantidade = Risco em $ / Distância do SL em $
                        notional_raw = (risk_amount / sl_price_diff) * cur_price
                        
                        # Limita a exposição máxima caso o stop fique muito apertado
                        max_notional = total_balance * 10 # Exp no máximo 10x a banca
                        notional = min(notional_raw, max_notional)
                        
                        # A alavancagem necessária é apenas para acomodar o Notional
                        margin_required = notional / leverage
                        if available_balance < margin_required:
                            log(f"⚠️ Saldo insuficiente para cobrir margem exigida do Risk Sizing. (Req: ${margin_required:.2f})")
                            break
                    
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
                                'entry': entry_price_executed, 'tp': tp_price, 'sl': sl_price, 'direction': direction,
                                'qty': qty, 'leverage': leverage, 'atr_pct': atr_pct, 'partial_taken': False,
                                'step_size': step_size_str
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
                        
                # Evita IP Ban da Binance (Weight Limit de 2400/min). Scan de 40 moedas consome 200 weight.
                await asyncio.sleep(15)
            
            except Exception as e:
                log(f"⚠️ Erro no Motor de Futuros: {e}")
                await asyncio.sleep(10)
            
    finally:
        bot_futures_running = False
        log("🛑 Encerrando tarefas em segundo plano do Motor de Futuros...")
        
        # [GRACEFUL SHUTDOWN] Limpa ordens remanescentes antes de desligar
        try:
            log("🧹 [SHUTDOWN] Varrendo ordens órfãs (Graceful Shutdown)...")
            open_orders = await client.futures_get_open_orders()
            if open_orders:
                symbols_with_orders = list(set(order['symbol'] for order in open_orders))
                positions_info = await client.futures_position_information()
                active_positions_map = {p['symbol']: float(p['positionAmt']) for p in positions_info}
                
                from core.futures_order_manager import robust_cancel_all_orders
                active_states = await futures_state.get_all()
                for sym in symbols_with_orders:
                    # Se não tem posição ativa na exchange E não está no state ativo
                    if active_positions_map.get(sym, 0.0) == 0.0 and sym not in active_states:
                        log(f"🧹 [SHUTDOWN] Ordem fantasma apagada em {sym}")
                        await robust_cancel_all_orders(client, sym, log)
        except Exception as e:
            if "-1003" not in str(e):
                log(f"⚠️ Erro no Graceful Shutdown: {e}")

        for task in futures_bg_tasks:
            if not task.done():
                task.cancel()
        futures_bg_tasks.clear()

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
