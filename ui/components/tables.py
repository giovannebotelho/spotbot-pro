def get_recent_trades_columns():
    return [
       {'name': 'date', 'label': 'Horário', 'field': 'date', 'align': 'left'},
       {'name': 'pair', 'label': 'Par', 'field': 'pair', 'align': 'left'},
       {'name': 'market', 'label': 'Mercado', 'field': 'market', 'align': 'center'},
       {'name': 'type', 'label': 'Direção', 'field': 'type', 'align': 'center'},
       {'name': 'pnl', 'label': 'PnL Líquido', 'field': 'pnl', 'align': 'right'},
    ]

def get_binance_positions_columns():
    return [
        {'name': 'symbol', 'label': 'Contrato / Par', 'field': 'symbol', 'align': 'left'},
        {'name': 'leverage', 'label': 'Alavancagem / Margem', 'field': 'leverage', 'align': 'center'},
        {'name': 'status', 'label': 'Estado', 'field': 'status', 'align': 'center'},
        {'name': 'pnl', 'label': 'Ganhos e Perdas Realizados (USDT)', 'field': 'pnl', 'align': 'right'},
        {'name': 'roi', 'label': 'ROI (%)', 'field': 'roi', 'align': 'right'},
        {'name': 'qty', 'label': 'Vol. Fechado', 'field': 'qty', 'align': 'right'},
        {'name': 'entry', 'label': 'Preço de Entrada', 'field': 'entry', 'align': 'right'},
        {'name': 'exit', 'label': 'Preço Médio de Fecho', 'field': 'exit', 'align': 'right'},
        {'name': 'time', 'label': 'Horário / Duração', 'field': 'time', 'align': 'right'},
    ]

def get_binance_orders_columns():
    return [
        {'name': 'time', 'label': 'Hora (BRT)', 'field': 'time', 'align': 'left'},
        {'name': 'symbol', 'label': 'Símbolo', 'field': 'symbol', 'align': 'left'},
        {'name': 'type', 'label': 'Tipo', 'field': 'type', 'align': 'center'},
        {'name': 'side', 'label': 'Compra/Venda', 'field': 'side', 'align': 'center'},
        {'name': 'avg_price', 'label': 'Preço Médio', 'field': 'avg_price', 'align': 'right'},
        {'name': 'price', 'label': 'Preço Ordem', 'field': 'price', 'align': 'right'},
        {'name': 'executed', 'label': 'Executado', 'field': 'executed', 'align': 'right'},
        {'name': 'value', 'label': 'Valor Total (USDT)', 'field': 'value', 'align': 'right'},
        {'name': 'reduce_only', 'label': 'Apenas Reduzir', 'field': 'reduce_only', 'align': 'center'},
        {'name': 'status', 'label': 'Estado', 'field': 'status', 'align': 'center'},
    ]
