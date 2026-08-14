import asyncio
from core.futures_state import futures_state
import datetime as dt_module
from config.settings import TELEGRAM_CONFIG, TRADING_CONFIG, TIMEZONE
from services.binance_client import get_futures_order_details, cancel_futures_order, get_bnb_price
from core.post_trade import create_data_row, save_to_csv
from services.telegram_notifier import send_telegram_message
from services.gemini_ai import generate_post_trade_synthesis
from utils.formatting import format_price

async def monitor_futures_lifecycle(
    client, bsm, symbol, position_side, entry_price, executed_qty, 
    tp_order_id, sl_order_id, tp_price, sl_price, db, 
    bot_futures_status_data, log=print, status=print
):
    """
    Monitora uma posição de futuros aberta.
    position_side: 'LONG' ou 'SHORT'
    """
    log(f"🛡️ Iniciando monitoramento de Futuros para {symbol} ({position_side})")
    
    use_ws_monitoring = True
    highest_price = entry_price
    lowest_price = entry_price
    
    async def check_and_handle_closure():
        try:
            pos_info = await client.futures_position_information(symbol=symbol)
            is_manually_closed = pos_info and float(pos_info[0]['positionAmt']) == 0.0
            
            tp_details = await get_futures_order_details(client, symbol, tp_order_id)
            sl_details = await get_futures_order_details(client, symbol, sl_order_id)
            
            if tp_details['status'] in ['FILLED'] or sl_details['status'] in ['FILLED'] or is_manually_closed:
                if is_manually_closed and tp_details['status'] != 'FILLED' and sl_details['status'] != 'FILLED':
                    log(f"⚠️ Posição de {symbol} foi fechada manualmente ou externamente!")
                    await close_futures_position(client, symbol, 'SELL' if position_side == 'LONG' else 'BUY', 0, tp_order_id, sl_order_id, log)
                    exit_price = float(bot_futures_status_data.get('price', entry_price))
                else:
                    log(f"🎯 Posição de Futuros fechada! TP: {tp_details['status']}, SL: {sl_details['status']}")
                    
                    if tp_details['status'] == 'FILLED':
                        await close_futures_position(client, symbol, 'SELL' if position_side == 'LONG' else 'BUY', 0, None, sl_order_id, log)
                        exit_price = float(tp_details.get('avgPrice', tp_details.get('actualPrice', 0))) if float(tp_details.get('avgPrice', tp_details.get('actualPrice', 0))) > 0 else float(tp_details.get('stopPrice', tp_details.get('triggerPrice', 0)))
                    else:
                        await close_futures_position(client, symbol, 'SELL' if position_side == 'LONG' else 'BUY', 0, tp_order_id, None, log)
                        exit_price = float(sl_details.get('avgPrice', sl_details.get('actualPrice', 0))) if float(sl_details.get('avgPrice', sl_details.get('actualPrice', 0))) > 0 else float(sl_details.get('stopPrice', sl_details.get('triggerPrice', 0)))

                realized_pnl = None
                try:
                    # Tenta puxar o PnL exato e preço de saída direto da Binance
                    recent_trades = await client.futures_account_trades(symbol=symbol, limit=10)
                    if recent_trades:
                        # Pega as últimas execuções que não têm realizedPnl zero
                        closing_trades = [t for t in recent_trades if float(t.get('realizedPnl', 0)) != 0]
                        if closing_trades:
                            realized_pnl = sum(float(t['realizedPnl']) for t in closing_trades[-3:])
                            # Opcional: ajustar o exit_price com base na última trade
                            exit_price = float(closing_trades[-1]['price'])
                except Exception as api_err:
                    log(f"Aviso ao buscar PnL real de {symbol}: {api_err}")

                await register_futures_trade(client, db, symbol, position_side, entry_price, exit_price, executed_qty, log, realized_pnl)
                return True
        except Exception as e:
            log(f"⚠️ Erro na verificação de posição: {e}")
        return False
    
    
    async def closure_checker():
        while True:
            await asyncio.sleep(5)
            if await check_and_handle_closure():
                break

    checker_task = asyncio.create_task(closure_checker())
    
    async def ws_loop():
        try:
            # WebSocket connection para klines/trades (Futuros)
            async with bsm.aggtrade_futures_socket(symbol=symbol.lower()) as ts:
                while True:
                    try:
                        msg = await asyncio.wait_for(ts.recv(), timeout=10.0)
                        
                        if 'p' in msg:
                            cur_price = float(msg['p'])
                            
                            if position_side == 'LONG':
                                if cur_price > highest_price:
                                    highest_price = cur_price
                            else:
                                if cur_price < lowest_price:
                                    lowest_price = cur_price
                                    
                            bot_futures_status_data['price'] = cur_price
                            
                            # Removido Trailing Stop Lock para aderir ao Grid Search Otimizado (Fase 2)
                    except asyncio.TimeoutError:
                        continue # Apenas continua esperando, sem falhar
        except Exception as ws_err:
            log(f"⚠️ WS Instável em Futuros para {symbol} ({ws_err}). Somente REST Polling ativo.")

    ws_task = asyncio.create_task(ws_loop())
    
    # Aguarda o checker finalizar (indica que a posição foi fechada por TP, SL ou manual)
    try:
        await checker_task
    except asyncio.CancelledError:
        pass
        
    ws_task.cancel()

    # Cleanup Global
    await futures_state.remove(symbol)
    bot_futures_status_data['active_symbols'] = list((await futures_state.get_all()).keys())
    return

async def robust_cancel_all_orders(client, symbol, log):
    try:
        await client.futures_cancel_all_open_orders(symbol=symbol)
    except Exception: pass
    
    try:
        open_orders = await client.futures_get_open_orders(symbol=symbol)
        for order in open_orders:
            try:
                await client.futures_cancel_order(symbol=symbol, orderId=order['orderId'])
            except Exception: pass
        if open_orders:
            log(f"🧹 [CLEANUP EXTREMO] Forçada a remoção de {len(open_orders)} ordens fantasmas de {symbol}.")
        else:
            log(f"🧹 [CLEANUP] Todas as ordens abertas para {symbol} foram canceladas.")
    except Exception as e:
        log(f"⚠️ Erro ao limpar ordens órfãs extremas de {symbol}: {e}")

async def close_futures_position(client, symbol, side, qty, tp_order, sl_order, log):
    """Fecha a posição a mercado e cancela todas as ordens orfãs."""
    await robust_cancel_all_orders(client, symbol, log)
    
    if qty > 0:
        try:
            await client.futures_create_order(symbol=symbol, side=side, type='MARKET', quantity=qty, reduceOnly='true')
        except Exception as e:
            if "ReduceOnly" not in str(e) and "-2022" not in str(e):
                log(f"⚠️ Erro ao fechar posição a mercado: {e}")

async def register_futures_trade(client, db, symbol, direction, entry, exit, qty, log, realized_pnl=None):
    """Registra o trade no DB e avisa no Telegram."""
    if realized_pnl is not None:
        gross_pnl = realized_pnl
    else:
        gross_pnl = (exit - entry) * qty if direction == 'LONG' else (entry - exit) * qty
        
    log(f"📈 Trade Futuros Concluído ({direction}): PnL Bruto = ${gross_pnl:.2f}")
    
    if TELEGRAM_CONFIG.get('bot_token') and TELEGRAM_CONFIG.get('chat_id'):
        emoji = "🟢" if gross_pnl > 0 else "🔴"
        await send_telegram_message(
            TELEGRAM_CONFIG['bot_token'], TELEGRAM_CONFIG['chat_id'],
            f"{emoji} <b>TRADE FUTUROS FINALIZADO</b>\n\n"
            f"🪙 Par: <b>{symbol}</b> ({direction})\n"
            f"📥 Entrada: {format_price(entry)}\n"
            f"📤 Saída: {format_price(exit)}\n"
            f"💰 PnL Bruto: <b>${gross_pnl:.2f}</b>"
        )
    
    if db:
        data = {
            "Símbolo": symbol,
            "Preço de Compra": entry,
            "Quantidade de Moeda": qty,
            "Meta de Lucro OCO": exit,
            "Data/Hora da Compra": dt_module.datetime.now(TIMEZONE).strftime("%d/%m/%Y at %H:%M:%S"),
            "Data/Hora OCO": dt_module.datetime.now(TIMEZONE).strftime("%d/%m/%Y %H:%M:%S"),
            "Resultado da Ordem OCO": "profit" if gross_pnl > 0 else "loss",
            "Resultado Parcial da Transação": gross_pnl,
            "Resultado Parcial da Transação Líquido": gross_pnl * 0.96,
            "Resultado Total Bruto": gross_pnl,
            "Resultado Total Liquido": gross_pnl * 0.96, # desconto de taxa ficticio
            "market_type": "FUTURES",
            "direction": direction,
            "margin_type": "ISOLATED",
            "leverage": 15
        }
        db.add_trade(data)

async def place_futures_trade_with_protection(client, symbol, side, qty, tp_price, sl_price, leverage, log):
    """Coloca ordem primária e as ordens condicionais de proteção (TP/SL)."""
    try:
        # 0. Clean Slate: Cancela TODAS as ordens fantasmas pendentes dessa moeda antes de entrar
        log(f"🧹 [PRÉ-TRADE] Limpando ordens antigas de {symbol} antes da entrada...")
        await robust_cancel_all_orders(client, symbol, log)
        
        # 1. Ordem de Entrada
        entry_order = await client.futures_create_order(
            symbol=symbol, side=side, type='MARKET', quantity=qty
        )
        # Calcula entry_price
        entry_price = 0.0
        if entry_order.get('avgPrice') and float(entry_order['avgPrice']) > 0:
            entry_price = float(entry_order['avgPrice'])
        else:
            entry_price = float((await client.futures_symbol_ticker(symbol=symbol))['price'])

        # Determina lado de saída
        exit_side = 'SELL' if side == 'BUY' else 'BUY'

        # 2. Ordem SL (Stop Market com closePosition)
        sl_order = await client.futures_create_order(
            symbol=symbol, side=exit_side, type='STOP_MARKET',
            stopPrice=sl_price, closePosition='true'
        )
        
        # 3. Ordem TP (Take Profit Market com closePosition)
        tp_order = await client.futures_create_order(
            symbol=symbol, side=exit_side, type='TAKE_PROFIT_MARKET',
            stopPrice=tp_price, closePosition='true'
        )
        
        log(f"✅ [FUTUROS] Posição {side} aberta em {symbol} a ${entry_price:.4f} (TP: ${tp_price}, SL: ${sl_price})")
        return entry_order, tp_order, sl_order, entry_price
    except Exception as e:
        log(f"⚠️ Erro ao posicionar trade em {symbol}: {e}")
        # Tentativa de cleanup caso falhe no meio
        try:
            await robust_cancel_all_orders(client, symbol, log)
            await client.futures_create_order(symbol=symbol, side='SELL' if side == 'BUY' else 'BUY', type='MARKET', quantity=qty, reduceOnly='true')
        except:
            pass
        return None, None, None, 0.0

async def handle_user_data_stream_event(client, db, event, log):
    """Processa eventos do WebSocket de usuário (ORDER_TRADE_UPDATE)."""
    from core.futures_engine import bot_futures_status_data
    
    if event.get('e') == 'ORDER_TRADE_UPDATE':
        order = event.get('o', {})
        symbol = order.get('s')
        status = order.get('X')
        order_type = order.get('o')
        
        if status == 'FILLED' and order_type in ['TAKE_PROFIT_MARKET', 'STOP_MARKET', 'MARKET', 'TRAILING_STOP_MARKET']:
            # Verifica se o símbolo está ativo para fechar e limpar
            active_futures_positions = await futures_state.get_all()
            if symbol in active_futures_positions:
                pos = await futures_state.remove(symbol)
                bot_futures_status_data['active_symbols'] = list((await futures_state.get_all()).keys())
                
                log(f"🎯 [UDS] Execução detectada para {symbol} ({status}). Limpando ordens órfãs...")
                await robust_cancel_all_orders(client, symbol, log)
                
                realized_pnl = float(order.get('rp', 0))
                exit_price = float(order.get('ap', 0))
                if exit_price == 0:
                    exit_price = float(order.get('sp', 0))
                    
                entry_price = pos.get('entry', 0.0)
                qty = pos.get('qty', 0.0)
                direction = pos.get('direction', 'LONG')
                
                await register_futures_trade(client, db, symbol, direction, entry_price, exit_price, qty, log, realized_pnl)

async def run_fallback_position_monitor(client, db, log):
    """Fallback de segurança que roda a cada 5 segundos para limpar posições se o WS falhar, e a cada 60s atua como Garbage Collector global."""
    from core.futures_engine import bot_futures_status_data
    import asyncio
    from core.futures_state import futures_state
    
    cycle_count = 0
    
    while True:
        await asyncio.sleep(5)
        cycle_count += 1
        active_futures_positions = await futures_state.get_all()
        symbols_to_check = list(active_futures_positions.keys())
        
        # 1. Fallback tradicional: checa se posições ativas fecharam "escondidas"
        if symbols_to_check:
            try:
                positions = await client.futures_position_information()
                for pos in positions:
                    symbol = pos['symbol']
                    if symbol in symbols_to_check and float(pos['positionAmt']) == 0.0:
                        # Posição fechou mas o WS não pegou!
                        log(f"⚠️ [FALLBACK] Posição finalizada detectada silenciosamente em {symbol}. Limpando ordens...")
                        
                        await robust_cancel_all_orders(client, symbol, log)
                        
                        pos_data = await futures_state.remove(symbol)
                        bot_futures_status_data['active_symbols'] = list((await futures_state.get_all()).keys())
                        
                        if pos_data:
                            # Busca o PnL exato das ordens recentes
                            realized_pnl = 0.0
                            exit_price = pos_data.get('entry', 0.0)
                            try:
                                recent_trades = await client.futures_account_trades(symbol=symbol, limit=5)
                                closing_trades = [t for t in recent_trades if float(t.get('realizedPnl', 0)) != 0]
                                if closing_trades:
                                    realized_pnl = sum(float(t['realizedPnl']) for t in closing_trades)
                                    exit_price = float(closing_trades[-1]['price'])
                            except: pass
                            
                            await register_futures_trade(client, db, symbol, pos_data['direction'], pos_data['entry'], exit_price, pos_data['qty'], log, realized_pnl)
            except Exception as e:
                log(f"⚠️ Erro no Fallback Monitor Principal: {e}")

        # 2. Garbage Collector Global (A cada 60 segundos)
        if cycle_count >= 12:
            cycle_count = 0
            try:
                open_orders = await client.futures_get_open_orders() # Weight 40
                if open_orders:
                    # Coleta símbolos com ordens abertas
                    symbols_with_orders = list(set(order['symbol'] for order in open_orders))
                    
                    if symbols_with_orders:
                        # Precisamos saber se esses símbolos têm posição aberta real, para não fechar ordens de posições válidas
                        positions_info = await client.futures_position_information()
                        active_positions_map = {p['symbol']: float(p['positionAmt']) for p in positions_info}
                        
                        for sym in symbols_with_orders:
                            # Se o bot NÃO estiver acompanhando essa moeda E ela NÃO tiver posição aberta na exchange
                            if sym not in symbols_to_check and active_positions_map.get(sym, 0.0) == 0.0:
                                log(f"🧹 [GARBAGE COLLECTOR] Ordens condicionais residuais (fantasmas) detectadas em {sym}! Limpando a exchange...")
                                await robust_cancel_all_orders(client, sym, log)
            except Exception as e:
                if "-1003" not in str(e): # Ignora se for apenas aviso de rate limit
                    log(f"⚠️ Erro no Garbage Collector Global: {e}")
