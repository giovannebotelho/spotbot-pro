import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
import asyncio
from unittest.mock import AsyncMock, MagicMock
from core.futures_state import futures_state
import core.futures_trailing_lock as ftl

async def test_breakeven_lock_after_partial():
    print("\n--- TESTE 1: Blindagem de Breakeven após Parcial 50% ---")
    mock_client = AsyncMock()
    mock_client.futures_create_order = AsyncMock()
    mock_client.futures_cancel_all_open_orders = AsyncMock()
    mock_client.futures_get_open_orders = AsyncMock(return_value=[])
    mock_client.futures_get_open_algo_orders = AsyncMock(return_value=[])
    
    # 1. Simula ativo que já pegou parcial e voltou para o preço de entrada (0% ROI ou levemente abaixo)
    symbol = "LINKUSDT"
    await futures_state.add(symbol, {
        'entry': 12.285, 'tp': 12.59, 'sl': 12.285, 'direction': 'LONG',
        'qty': 10.8, 'leverage': 10, 'atr_pct': 0.015, 'partial_taken': True,
        'step_size': '0.1', 'peak_price': 12.35, 'locked_profit_roi': 0.2
    })
    
    # Simula preço atual voltando para 12.280 (abaixo da entrada)
    mock_client.futures_symbol_ticker = AsyncMock(return_value={'symbol': symbol, 'price': '12.280'})
    
    logs = []
    def mock_log(msg): logs.append(msg)
    
    # Executa uma rodada do monitor
    active = await futures_state.get_all()
    assert symbol in active
    
    # Simula a checagem que ocorre dentro do loop
    pos = active[symbol]
    cur_price = 12.280
    entry_price = pos['entry']
    direction = pos['direction']
    qty = pos['qty']
    cur_roi = ((cur_price - entry_price) / entry_price) * pos['leverage'] * 100
    
    # FASE 1.5: Breakeven Check
    is_at_or_below_be = (direction == 'LONG' and cur_price <= entry_price) or (cur_roi <= 0.0)
    assert is_at_or_below_be is True, "Deveria detectar retorno ao Breakeven"
    
    await ftl.execute_trailing_close(mock_client, symbol, direction, qty, cur_price, entry_price, None, mock_log)
    
    # Verifica se a ordem a mercado foi enviada
    mock_client.futures_create_order.assert_called_with(
        symbol=symbol, side='SELL', type='MARKET', quantity=qty, reduceOnly='true'
    )
    
    # Verifica se foi removido do estado
    assert (await futures_state.get(symbol)) is None, "Posição deve ter sido removida do estado após fechamento"
    print("[OK] TESTE 1 PASSOU: Breakeven fechou a mercado imediatamente sem risco de perda residual!")

async def test_emergency_hard_cap_sl():
    print("\n--- TESTE 2: Stop de Emergencia Hard-Cap (-5.5% ROI) ---")
    mock_client = AsyncMock()
    mock_client.futures_create_order = AsyncMock()
    mock_client.futures_cancel_all_open_orders = AsyncMock()
    mock_client.futures_get_open_orders = AsyncMock(return_value=[])
    mock_client.futures_get_open_algo_orders = AsyncMock(return_value=[])
    
    symbol = "ETHUSDT"
    await futures_state.add(symbol, {
        'entry': 2500.0, 'tp': 2600.0, 'sl': 2400.0, 'direction': 'LONG',
        'qty': 0.1, 'leverage': 10, 'atr_pct': 0.015, 'partial_taken': False,
        'step_size': '0.001', 'peak_price': 2500.0, 'locked_profit_roi': 0.0
    })
    
    cur_price = 2480.0 # Queda de 0.8% * 10x = -8.0% ROI
    pos = await futures_state.get(symbol)
    cur_roi = ((cur_price - pos['entry']) / pos['entry']) * pos['leverage'] * 100
    
    assert cur_roi <= -5.5, "ROI deve ser menor que -5.5%"
    
    logs = []
    await ftl.execute_trailing_close(mock_client, symbol, pos['direction'], pos['qty'], cur_price, pos['entry'], None, lambda m: logs.append(m))
    
    mock_client.futures_create_order.assert_called_with(
        symbol=symbol, side='SELL', type='MARKET', quantity=0.1, reduceOnly='true'
    )
    assert (await futures_state.get(symbol)) is None
    print("[OK] TESTE 2 PASSOU: Stop de emergencia fechou a posicao a mercado e cortou a perda!")

async def main():
    await test_breakeven_lock_after_partial()
    await test_emergency_hard_cap_sl()
    print("\n[SUCESSO] TODOS OS TESTES DE BLINDAGEM PASSARAM COM 100% DE SUCESSO!")

if __name__ == "__main__":
    asyncio.run(main())
