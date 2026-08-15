import itertools
import concurrent.futures
from backtest.engine import load_data, calculate_indicators, Backtester
import pandas as pd

def evaluate_params(args):
    df_15m, sl_mult, tp_mult, max_roi, trail_rate = args
    tester = Backtester(
        df=df_15m, 
        initial_balance=100.0, 
        risk_per_trade=0.02,
        sl_multiplier=sl_mult,
        tp_multiplier=tp_mult,
        max_roi=max_roi,
        trailing_rate=trail_rate
    )
    equity_df, trades = tester.run()
    
    if not trades:
        return None
        
    final_balance = equity_df['equity'].iloc[-1]
    
    # Calculate Max Drawdown
    equity_series = equity_df['equity']
    peak = equity_series.expanding(min_periods=1).max()
    drawdown = (equity_series - peak) / peak
    max_drawdown = drawdown.min()
    
    win_trades = [t for t in trades if t['pnl'] > 0]
    win_rate = len(win_trades) / len(trades)
    
    return {
        'sl_mult': sl_mult,
        'tp_mult': tp_mult,
        'max_roi': max_roi,
        'trail_rate': trail_rate,
        'final_balance': final_balance,
        'max_drawdown': max_drawdown * 100,
        'win_rate': win_rate * 100,
        'total_trades': len(trades)
    }

def run_optimizer():
    print("[1] Carregando dados do laboratorio...")
    df_15m = load_data('BTCUSDT', '15m')
    df_1h = load_data('BTCUSDT', '1h')
    df_15m = calculate_indicators(df_15m, df_1h)
    
    print("[2] Preparando matriz de otimizacao (Grid Search)...")
    sl_multipliers = [1.0, 1.5, 2.0, 2.5]
    tp_multipliers = [1.0, 1.5, 2.0, 3.0]
    max_rois = [0.05, 0.10, 0.15]
    trail_rates = [0.01, 0.015, 0.02]
    
    combinations = list(itertools.product(sl_multipliers, tp_multipliers, max_rois, trail_rates))
    args_list = [(df_15m, *combo) for combo in combinations]
    
    print(f"[3] Iniciando Forca Bruta para {len(combinations)} combinacoes...")
    
    results = []
    with concurrent.futures.ProcessPoolExecutor() as executor:
        for res in executor.map(evaluate_params, args_list):
            if res:
                results.append(res)
                
    results_df = pd.DataFrame(results)
    
    profitable = results_df[results_df['final_balance'] > 100]
    
    if profitable.empty:
        print("Nenhuma configuracao deu lucro.")
        return
        
    print("\n[TOP 5 LUCRO]")
    top_profit = profitable.sort_values(by='final_balance', ascending=False).head(5)
    print(top_profit.to_string(index=False))
    
    print("\n[TOP 5 MENOR RISCO (DRAWDOWN)]")
    top_safe = profitable.sort_values(by='max_drawdown', ascending=False).head(5)
    print(top_safe.to_string(index=False))

if __name__ == '__main__':
    run_optimizer()
