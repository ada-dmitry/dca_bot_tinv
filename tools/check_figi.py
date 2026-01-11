import os
import sys

from dotenv import load_dotenv
from t_tech.invest import Client, InstrumentIdType
from t_tech.invest.utils import quotation_to_decimal

FIGI = [
    "BBG00425VG07",
    "BBG0073DLHS1",
]

load_dotenv()
token = os.environ.get("TINKOFF_TOKEN")
if not token:
    print("ERROR: TINKOFF_TOKEN is not set", file=sys.stderr)
    sys.exit(2)

with Client(token) as client:
    # Проверка карточек
    for f in FIGI:
        inst = client.instruments.get_instrument_by(
            id_type=InstrumentIdType.INSTRUMENT_ID_TYPE_FIGI, id=f
        ).instrument
        if not inst:
            print(f"{f}: NOT FOUND")
            continue
        print(
            f"{f}: {inst.ticker} | {inst.name} | lot={inst.lot} | curr={inst.currency}"
        )

    # Проверка last price
    lp = client.market_data.get_last_prices(figi=FIGI)
    seen = set()
    for p in lp.last_prices:
        price = quotation_to_decimal(p.price)
        print(f"PRICE {p.figi}: {price}")
        seen.add(p.figi)
    missing = [f for f in FIGI if f not in seen]
    if missing:
        print("Нет last price для:", missing)
