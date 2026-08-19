import asyncio
import time

from core.futures_state import futures_state
from core.futures_order_manager import get_futures_order_details, robust_cancel_all_orders

async def run_trailing_lock_monitor(client, log=print):
    """
    Monitora posições ativas de futuros a cada 1 segundo.
    Se o preço atingir 80% da meta (TP), engatilha a Trava Trailing.
    Se, após engatilhar, o preço recuar 0.50% a partir do pico, fecha a mercado.
    """
    log("🛡️ Trailing Lock Monitor iniciado...")
    while True:
        try:
            active_futures_positions = await futures_state.get_all()
            symbols_to_check = list(active_futures_positions.keys())
            if not symbols_to_check:
                await asyncio.sleep(2)
                continue
                
            # Busca o preço atual apenas dos tickers ativos (Otimização de Rate Limit)
            tasks = [client.futures_symbol_ticker(symbol=s) for s in symbols_to_check]
            tickers = await asyncio.gather(*tasks, return_exceptions=True)
            
            price_map = {}
            for t in tickers:
                if isinstance(t, dict) and 'symbol' in t and 'price' in t:
                    price_map[t['symbol']] = float(t['price'])
            
            for symbol in symbols_to_check:
                if symbol not in active_futures_positions:
                    continue
                    
                pos = active_futures_positions[symbol]
                direction = pos['direction']
                entry_price = pos['entry']
                tp_price = pos['tp']
                qty = pos.get('qty', 0)
                
                cur_price = price_map.get(symbol)
                if not cur_price:
                    continue
                
                leverage = pos.get('leverage', 15)
                
                # Inicializa trackers de trailing se não existirem
                if 'peak_price' not in pos:
                    pos['peak_price'] = cur_price
                    
                # Atualiza o pico
                if direction == 'LONG':
                    if cur_price > pos['peak_price']:
                        pos['peak_price'] = cur_price
                else: # SHORT
                    if cur_price < pos['peak_price'] or pos['peak_price'] == 0:
                        pos['peak_price'] = cur_price
                        
                # Calcula ROIs (em porcentagem)
                if direction == 'LONG':
                    cur_roi = ((cur_price - entry_price) / entry_price) * leverage * 100
                    peak_roi = ((pos['peak_price'] - entry_price) / entry_price) * leverage * 100
                else:
                    cur_roi = ((entry_price - cur_price) / entry_price) * leverage * 100
                    peak_roi = ((entry_price - pos['peak_price']) / entry_price) * leverage * 100
                
                atr_pct = pos.get('atr_pct', 0.015)
                partial_taken = pos.get('partial_taken', False)

                # Cálculo de Metas Adaptativas por Volatilidade (ATR Dinâmico)
                # Volatilidade alta -> alvos maiores; Volatilidade baixa -> alvos curtos
                adaptive_tp_roi = max(4.5, min(12.0, atr_pct * leverage * 100 * 0.9))
                adaptive_preventive_sl = -max(7.5, min(14.0, atr_pct * leverage * 100 * 1.1))

                # Regra 0: Parcial Dinâmica (50% do lote em +3.5% ROI) + Breakeven Automático
                if cur_roi >= 3.5 and not partial_taken and qty > 0:
                    try:
                        half_qty = qty / 2.0
                        step_size_str = pos.get('step_size', '0.001')
                        from decimal import Decimal, ROUND_DOWN
                        step_dec = Decimal(step_size_str)
                        half_qty_dec = Decimal(str(half_qty))
                        quantized_half = (half_qty_dec / step_dec).quantize(Decimal('1'), rounding=ROUND_DOWN) * step_dec
                        half_qty_rounded = float(quantized_half)
                        
                        if half_qty_rounded > 0:
                            side_exit = 'SELL' if direction == 'LONG' else 'BUY'
                            log(f"🎯 [PARCIAL 50%] {symbol} atingiu +{cur_roi:.2f}% de ROI! Realizando 50% ({half_qty_rounded}) a mercado...")
                            
                            # Envia ordem parcial a mercado
                            await client.futures_create_order(
                                symbol=symbol, side=side_exit, type='MARKET',
                                quantity=half_qty_rounded, reduceOnly='true'
                            )
                            
                            # Cancela SL antigo e recria SL no Breakeven (Preço de Entrada) para o restante
                            await robust_cancel_all_orders(client, symbol, log)
                            remaining_qty = qty - half_qty_rounded
                            
                            await client.futures_create_order(
                                symbol=symbol, side=side_exit, type='STOP_MARKET',
                                stopPrice=entry_price, closePosition='true'
                            )
                            
                            # Atualiza estado na memória
                            await futures_state.update(symbol, 'qty', remaining_qty)
                            await futures_state.update(symbol, 'partial_taken', True)
                            await futures_state.update(symbol, 'sl', entry_price)
                            
                            log(f"🛡️ [BREAKEVEN] Stop Loss de {symbol} movido para o preço de entrada (${entry_price:.4f}). Risco ZERO ativado!")
                            
                            from config.settings import TELEGRAM_CONFIG
                            from services.telegram_notifier import send_telegram_message
                            if TELEGRAM_CONFIG.get('bot_token') and TELEGRAM_CONFIG.get('chat_id'):
                                asyncio.create_task(send_telegram_message(
                                    TELEGRAM_CONFIG['bot_token'], TELEGRAM_CONFIG['chat_id'],
                                    f"🎯 <b>PARCIAL 50% EXECUTADA NO LUCRO!</b>\n\n"
                                    f"🪙 <b>Par:</b> {symbol} ({direction})\n"
                                    f"📈 <b>ROI Atual:</b> +{cur_roi:.2f}%\n"
                                    f"💰 <b>Qtd Fechada:</b> {half_qty_rounded}\n"
                                    f"🛡️ <b>Stop Breakeven:</b> Movido para ${entry_price:.4f} (Risco Zero!)"
                                ))
                            continue
                    except Exception as p_err:
                        log(f"⚠️ Aviso na execução parcial de {symbol}: {p_err}")

                # Regra 1: Take Profit Absoluto / Adaptativo por ATR
                if cur_roi >= adaptive_tp_roi:
                    log(f"🎯 [TAKE-PROFIT MAX] {symbol} atingiu o alvo adaptativo de {cur_roi:.2f}% de ROI (Meta: {adaptive_tp_roi:.1f}%)! Fechando a mercado.")
                    await execute_trailing_close(client, symbol, direction, qty, log)
                    continue
                    
                # Regra 2: Trailing Lock Dinâmico Flexível
                if peak_roi >= 3.0:
                    # Se o trade ainda está no positivo, o trailing é mais ágil (distância de 3%)
                    if cur_roi >= 0:
                        if (peak_roi - cur_roi) >= 3.0:
                            log(f"⚡ [TRAILING-LOCK] Recuo detectado no lucro (Pico: {peak_roi:.2f}%, Atual: {cur_roi:.2f}%). Garantindo a operação!")
                            await execute_trailing_close(client, symbol, direction, qty, log)
                            continue
                            
                # Regra 3: Tolerância Máxima Negativa (Stop Preventivo Adaptativo)
                if cur_roi <= adaptive_preventive_sl:
                    log(f"⚡ [STOP PREVENTIVO] Trade atingiu tolerância máxima negativa (Atual: {cur_roi:.2f}%, Limite: {adaptive_preventive_sl:.1f}%). Fechando antes do SL rígido!")
                    await execute_trailing_close(client, symbol, direction, qty, log)
                    continue
                            
        except Exception as e:
            log(f"⚠️ Erro no Trailing Lock Monitor: {e}")
            
        await asyncio.sleep(1)

async def execute_trailing_close(client, symbol, direction, qty, log):
    try:
        side_exit = 'SELL' if direction == 'LONG' else 'BUY'
        
        # 1. Cancela TP/SL antigos para evitar órfãs e liberar margem ANTES de fechar
        await robust_cancel_all_orders(client, symbol, log)
        
        # 2. Envia a mercado
        try:
            await client.futures_create_order(symbol=symbol, side=side_exit, type='MARKET', quantity=qty, reduceOnly='true')
            log(f"✅ [TRAILING-LOCK] Posição de {symbol} fechada com sucesso em segurança.")
        except Exception as e:
            if "ReduceOnly" not in str(e) and "-2022" not in str(e):
                log(f"⚠️ Erro ao enviar ordem a mercado no Trailing Lock: {e}")
            else:
                log(f"✅ [TRAILING-LOCK] Posição já parece estar fechada.")
                
    except Exception as e:
        log(f"❌ Erro crítico no Trailing Lock de {symbol}: {e}")
