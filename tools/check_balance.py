# check_balance.py
from dotenv import load_dotenv
from tinkoff.invest import Client
from tinkoff.invest.utils import quotation_to_decimal
from decimal import Decimal
import os
import sys


def dec_money(mv) -> Decimal:
    # Работает и для MoneyValue, и для Quotation
    return quotation_to_decimal(mv) if mv is not None else Decimal(0)


load_dotenv()
token = os.environ.get("TINKOFF_TOKEN")
account_id = os.environ.get("TINKOFF_ACCOUNT_ID")

if not token:
    print("ERROR: TINKOFF_TOKEN is not set")
    sys.exit(2)

with Client(token) as client:
    # Если account_id не задан, берём первый реальный счёт
    if not account_id:
        accs = client.users.get_accounts().accounts
        if not accs:
            print(
                "Нет реальных счетов. Проверь права токена: accounts:read и наличие счёта.")
            sys.exit(3)
        account_id = accs[0].id

    # Универсальный способ: get_positions -> money (остатки) и blocked (заморожено)
    pos = client.operations.get_positions(account_id=account_id)

    rub_money = next(
        (m for m in pos.money if m.currency.lower() == "rub"), None)
    rub_blocked = next(
        (m for m in pos.blocked if m.currency.lower() == "rub"), None)

    rub_free = dec_money(rub_money) if rub_money else Decimal(0)
    rub_frozen = dec_money(rub_blocked) if rub_blocked else Decimal(0)
    rub_available = rub_free - rub_frozen

    print("ACCOUNT_ID:", account_id)
    print("RUB free:     ", rub_free)
    print("RUB blocked:  ", rub_frozen)
    print("RUB available:", rub_available)

    # Дополнительно: оценка портфеля (если нужно)
    pf = client.operations.get_portfolio(account_id=account_id)
    total_currencies = dec_money(pf.total_amount_currencies) if hasattr(
        pf, "total_amount_currencies") else None
    if total_currencies is not None:
        print("Portfolio total_amount_currencies:", total_currencies)
