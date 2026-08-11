import asyncio
import sys
import os

# Adiciona o diretório principal ao path para conseguirmos importar os módulos do projeto
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from binance import AsyncClient
from binance.exceptions import BinanceAPIException
from config.settings import API_KEYS

async def run_tests():
    print("==================================================")
    print("🔍 INICIANDO TESTES DE CONEXÃO E ORDENS - BINANCE")
    print("==================================================")
    
    api_key = API_KEYS['mainnet']['key']
    api_secret = API_KEYS['mainnet']['secret']
    
    if not api_key or not api_secret:
        print("❌ ERRO: Chaves de API da Mainnet não configuradas no .env!")
        return
        
    client = await AsyncClient.create(api_key, api_secret)
    
    try:
        # 1. Teste de Ping
        print("\n[1] Testando conexão com a Binance...")
        await client.ping()
        print("✅ Ping OK!")
        
        # 2. Teste de Saldo SPOT
        print("\n[2] Buscando Saldo SPOT (USDT)...")
        spot_balance_info = await client.get_asset_balance(asset='USDT')
        spot_balance = float(spot_balance_info['free']) if spot_balance_info else 0.0
        print(f"💰 Saldo SPOT Disponível: {spot_balance:.2f} USDT")
        
        # 3. Teste de Saldo FUTURES
        print("\n[3] Buscando Saldo FUTURES (USDT)...")
        futures_info = await client.futures_account()
        futures_balance = 0.0
        for asset in futures_info.get('assets', []):
            if asset['asset'] == 'USDT':
                futures_balance = float(asset['availableBalance'])
                break
        print(f"💰 Saldo FUTURES Disponível: {futures_balance:.2f} USDT")
        
        # 4. Teste de Ordem SPOT (DRY RUN)
        print("\n[4] Simulando Ordem SPOT (create_test_order) para BTCUSDT...")
        try:
            # Pega o preço atual do BTC
            ticker = await client.get_symbol_ticker(symbol='BTCUSDT')
            price = float(ticker['price'])
            buy_price = round(price * 0.99, 2) # Tenta comprar 1% abaixo do preço para passar no PERCENT_PRICE filter
            
            test_order = await client.create_test_order(
                symbol='BTCUSDT',
                side='BUY',
                type='LIMIT',
                timeInForce='GTC',
                quantity=0.001,
                price=str(buy_price)
            )
            print(f"✅ Ordem Spot de TESTE validada com sucesso pela Binance! (Não executada)")
            print(f"Detalhes simulados: Comprar 0.001 BTC a {buy_price}")
        except BinanceAPIException as e:
            print(f"❌ Erro ao validar ordem SPOT de teste: {e}")
            
        # 5. Teste de Setup de Margem e Alavancagem FUTURES
        print("\n[5] Testando Ajuste de Alavancagem FUTURES (BTCUSDT a 20x)...")
        try:
            # Tenta mudar para Isolada
            try:
                await client.futures_change_margin_type(symbol='BTCUSDT', marginType='ISOLATED')
                print("✅ Margem alterada para ISOLATED.")
            except BinanceAPIException as e:
                if e.code == -4046:
                    print("✅ Margem já estava como ISOLATED.")
                else:
                    raise e
                    
            # Ajusta Alavancagem
            lev_resp = await client.futures_change_leverage(symbol='BTCUSDT', leverage=20)
            print(f"✅ Alavancagem ajustada para: {lev_resp['leverage']}x")
        except BinanceAPIException as e:
            print(f"❌ Erro ao configurar Futuros: {e}")
            
        # 6. Teste de Ordem FUTURES (Criação e Cancelamento Imediato)
        print("\n[6] Testando Ordem FUTURES Real com Cancelamento Imediato...")
        print("⚠️  Aviso: Isso criará uma ordem LIMIT de compra de 0.006 BTC a $10.000 (Notional $60) e cancelará imediatamente.")
        try:
            order = await client.futures_create_order(
                symbol='BTCUSDT',
                side='BUY',
                type='LIMIT',
                timeInForce='GTC',
                quantity=0.006,
                price='10000.00'
            )
            order_id = order['orderId']
            print(f"✅ Ordem LIMIT criada com sucesso! ID: {order_id}")
            print(f"Resposta bruta da Binance: {order}")
            
            # Cancela imediatamente
            print("Cancelando ordem de teste...")
            cancel_res = await client.futures_cancel_order(symbol='BTCUSDT', orderId=order_id)
            print(f"✅ Ordem cancelada com sucesso! Status: {cancel_res['status']}")
            
        except BinanceAPIException as e:
            print(f"❌ Erro ao testar ordem FUTURES: {e}")
            
    except Exception as e:
        print(f"❌ ERRO GERAL: {e}")
    finally:
        await client.close_connection()
        print("\n==================================================")
        print("🏁 TESTES FINALIZADOS")
        print("==================================================")

if __name__ == "__main__":
    asyncio.run(run_tests())
