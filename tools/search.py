import os

from dotenv import load_dotenv
from t_tech.invest import Client

load_dotenv()
TOKEN = os.getenv("TINKOFF_TOKEN")


with Client(TOKEN) as client:
    # Все акции
    shares = client.instruments.shares()
    for s in shares.instruments:
        if s.ticker == "TGLD@":  # ищем по тикеру
            print(s.ticker, s.figi, s.name)

    # Все облигации
    bonds = client.instruments.bonds()
    for b in bonds.instruments:
        if "ОФЗ 26212" in b.name:  # ищем все ОФЗ по названию
            print(b.name, b.figi)

    # Все ETF
    etfs = client.instruments.etfs()
    for e in etfs.instruments:
        if "TPAY" in e.ticker:
            print(e.ticker, e.figi, e.name)

    # Поиск валют по тикеру/названию и вывод figi
    wanted = ["USD", "EUR", "GBP", "JPY"]
    currencies = client.instruments.currencies()
    for cur in currencies.instruments:
        for w in wanted:
            if w in (cur.ticker or "") or w in (cur.name or ""):
                print(cur.ticker, cur.figi, cur.name)
                break

    # Поиск золота по тикеру (ищем среди валют, фьючерсов, ETF и акций)
    gold_tickers = ["TGLD@"]
    lists_to_search = [
        client.instruments.currencies(),
        client.instruments.futures(),
        client.instruments.etfs(),
        client.instruments.shares(),
    ]
    for lst in lists_to_search:
        for inst in lst.instruments:
            for gt in gold_tickers:
                if gt in (inst.ticker or "") or gt in (inst.name or ""):
                    print(inst.ticker, inst.figi, inst.name)
                    break
