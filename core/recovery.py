import asyncio
import pandas as pd
import datetime as dt_module
from zoneinfo import ZoneInfo
from config.settings import TELEGRAM_CONFIG, TRADING_CONFIG, MAX_CONCURRENT_POSITIONS, TIMEZONE, PAPER_TRADING_MODE
from services.binance_client import get_order_details
from core.order_manager import monitor_oco_lifecycle
from services.telegram_notifier import send_telegram_message

async def recover_state(client, bsm, db, log, status, saldo_inicial_usdt, active_positions, bot_status_data, shared_market_data, active_monitoring_tasks, globals_dict):
    if PAPER_TRADING_MODE:
        return
        
    # Verificação e Adotação Multi-Posição de Ordens OCO Ativas (State Recovery Engine v4.0)
    open_ocos = await client.get_open_oco_orders()
        
    if open_ocos:
        log(f"🔄 \033[1;36mState Recovery Engine\033[0m: {len(open_ocos)} ordem(ns) OCO ativa(s) encontrada(s) na Binance!")
        for oco_order in open_ocos[:MAX_CONCURRENT_POSITIONS]:
            active_target_symbol = oco_order['symbol']
            log(f"🛡️ Retomando monitoramento paralelo de \033[1;33m{active_target_symbol}\033[0m sem cancelar a operação...")
            
            if TELEGRAM_CONFIG.get('bot_token') and TELEGRAM_CONFIG.get('chat_id'):
                asyncio.create_task(send_telegram_message(
                    TELEGRAM_CONFIG['bot_token'], TELEGRAM_CONFIG['chat_id'],
                    f"🔄 <b>State Recovery Ativado!</b>\n\n"
                    f"🪙 Par: <b>{active_target_symbol}</b>\n"
                    f"🛡️ Ordem OCO recuperada da Binance. Retomando monitoramento de lucro e stop automaticamente!"
                ))
            
            # Para ordens recuperadas, não temos o score exato nem slippage originais sem buscar do BD, entao deixaremos 0.0
            rec_confluence_score = 0.0
            rec_slippage = 0.0

            limit_order_id = oco_order['orders'][1]['orderId']
            stop_order_id = oco_order['orders'][0]['orderId']
            target_symbol_info = await client.get_symbol_info(active_target_symbol)
            tick_size = float(next(f for f in target_symbol_info['filters'] if f['filterType'] == 'PRICE_FILTER')['tickSize'])
            step_size = float(next(f for f in target_symbol_info['filters'] if f['filterType'] == 'LOT_SIZE')['stepSize'])
            
            limit_details = await get_order_details(client, active_target_symbol, limit_order_id)
            stop_details = await get_order_details(client, active_target_symbol, stop_order_id)
            
            executed_qty = float(limit_details.get('origQty', stop_details.get('origQty', 0)))
            lucro_alvo = float(limit_details.get('price', 0))
            stop_loss = float(stop_details.get('stopPrice', stop_details.get('price', 0)))
            
            price = 0.0
            try:
                all_orders = await client.get_all_orders(symbol=active_target_symbol, limit=20)
                # Busca a última ordem de COMPRA preenchida
                buy_orders = [o for o in all_orders if o['side'] == 'BUY' and o['status'] == 'FILLED']
                if buy_orders:
                    last_buy = buy_orders[-1]
                    price = float(last_buy['cummulativeQuoteQty']) / float(last_buy['executedQty']) if float(last_buy['executedQty']) > 0 else float(last_buy['price'])
                    if price == 0.0:
                        # Fallback if price is 0 for market orders where price field is 0
                        ticker_cur = await client.get_symbol_ticker(symbol=active_target_symbol)
                        price = float(ticker_cur['price'])
                else:
                    ticker_cur = await client.get_symbol_ticker(symbol=active_target_symbol)
                    price = float(ticker_cur['price'])
            except Exception as e:
                log(f"⚠️ Aviso ao buscar preço de entrada histórico para {active_target_symbol}: {e}")
                ticker_cur = await client.get_symbol_ticker(symbol=active_target_symbol)
                price = float(ticker_cur['price'])
                
            order_val_usdt = round(executed_qty * price, 2)
            purchase_timestamp = dt_module.datetime.now(TIMEZONE).strftime("%d/%m/%Y at %H:%M:%S")
            active_positions[active_target_symbol] = {
                'entry': price,
                'tp': lucro_alvo,
                'sl': stop_loss,
                'qty': executed_qty,
                'time': purchase_timestamp
            }
            bot_status_data['active_symbols'] = list(active_positions.keys())
            bot_status_data['target_asset'] = active_target_symbol
            bot_status_data['price'] = price
            bot_status_data['tp_price'] = lucro_alvo
            bot_status_data['sl_price'] = stop_loss
            bot_status_data['entry_price'] = price

            # Carrega klines e indicadores para popular o gráfico do ativo recuperado
            try:
                klines_raw = await client.get_klines(symbol=active_target_symbol, interval=TRADING_CONFIG['interval'], limit=100)
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

                    shared_market_data['dates'] = dates_rec
                    shared_market_data['klines'] = [[float(k[1]), float(k[4]), float(k[3]), float(k[2])] for k in klines_raw]
                    shared_market_data['bb_upper'] = bb_upper[-100:]
                    shared_market_data['bb_lower'] = bb_lower[-100:]
                    shared_market_data['ema200'] = ema200[-100:]
                    shared_market_data['volumes'] = volumes_rec
            except Exception as k_err:
                log(f"⚠️ Aviso ao carregar klines no State Recovery ({active_target_symbol}): {k_err}")

            rec_confluence_score = 0.0
            rec_slippage = 0.0

            # Lança o monitoramento em background para NÃO bloquear o scanner das vagas restantes!
            task = asyncio.create_task(monitor_oco_lifecycle(
                client, bsm, active_target_symbol, oco_order, limit_order_id, stop_order_id,
                price, executed_qty, order_val_usdt, lucro_alvo, stop_loss, target_symbol_info,
                tick_size, step_size, log, status, saldo_inicial_usdt, 1, purchase_timestamp,
                "State Recovery (Posição Retomada)", 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
                [], 0, 0, 0, 0, 0, 0, True, None, db, rec_confluence_score, rec_slippage,
                active_positions, bot_status_data, globals_dict
            ))
            active_monitoring_tasks.append(task)


