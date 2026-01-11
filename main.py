from __future__ import annotations

import argparse
import csv
import datetime as dt
import os
import sys
import time
from decimal import ROUND_DOWN, Decimal
from typing import Any, Dict, List, Optional
from uuid import uuid4

import yaml
from dotenv import load_dotenv
from t_tech.invest import (
    Client,
    InstrumentIdType,
    MoneyValue,
    OrderDirection,
    OrderType,
    Quotation,
)
from t_tech.invest.exceptions import RequestError
from t_tech.invest.utils import quotation_to_decimal

CSV_LOG = "orders_log.csv"


# ----------------------------- utils -----------------------------
def money_to_decimal(v: MoneyValue) -> Decimal:
    return Decimal(v.units) + (Decimal(v.nano) / Decimal(1_000_000_000))


def dec(v: Quotation | MoneyValue | None, default: str = "0") -> Decimal:
    """Безопасное преобразование Quotation/MoneyValue -> Decimal."""
    if v is None:
        return Decimal(default)
    # корректно обрабатываем как Quotation, так и MoneyValue
    if isinstance(v, Quotation):
        return quotation_to_decimal(v)
    if isinstance(v, MoneyValue):
        return money_to_decimal(v)
    return Decimal(default)


def ensure_csv(path: str):
    if not os.path.exists(path):
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(
                [
                    "ts",
                    "period_key",
                    "figi",
                    "ticker",
                    "name",
                    "instrument_type",
                    "lots",
                    "lot_size",
                    "filled_price_per_share",
                    "cost_rub",
                    "status",
                    "order_request_id",
                ]
            )


def append_log(**kwargs):
    ensure_csv(CSV_LOG)
    with open(CSV_LOG, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(
            [
                dt.datetime.now(dt.timezone.utc).isoformat(),
                kwargs.get("period_key", ""),
                kwargs.get("figi", ""),
                kwargs.get("ticker", ""),
                kwargs.get("name", ""),
                kwargs.get("instrument_type", ""),
                kwargs.get("lots", 0),
                kwargs.get("lot_size", 0),
                str(kwargs.get("filled_price_per_share", "")),
                str(kwargs.get("cost_rub", "")),
                kwargs.get("status", ""),
                kwargs.get("order_request_id", ""),
            ]
        )


def insert_report_separator():
    """Добавляет пустую строку в CSV перед новой серией транзакций."""
    ensure_csv(CSV_LOG)
    with open(CSV_LOG, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow([])  # просто пустая строка


# ----------------------------- config -----------------------------
def load_config(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    assets = cfg.get("assets", [])
    if not assets:
        raise ValueError(
            "Config must include non-empty 'assets' list under key 'assets'"
        )
    total_w = sum(float(a.get("weight", 0.0)) for a in assets)
    if abs(total_w - 1.0) > 1e-6:
        raise ValueError(f"Sum of weights must be 1.0; got {total_w}")
    for a in assets:
        if "figi" not in a or "weight" not in a:
            raise ValueError("Each asset must have 'figi' and 'weight' fields")
    return cfg


# ----------------------------- market helpers -----------------------------
def pick_account_id(client: Any, explicit: Optional[str]) -> str:
    """Реальный брокерский счёт (без песочницы)."""
    if explicit:
        return explicit
    accs = list(client.users.get_accounts().accounts)
    if not accs:
        raise RuntimeError(
            "Нет реальных брокерских счетов. Проверь токен/права или создай счёт."
        )
    return accs[0].id


def get_instrument_meta(client: Any, figi: str) -> dict:
    """Мини‑карточка инструмента: лот, валюта, тип, UID, и (если доступны) nominal/aci для облигаций."""
    resp = client.instruments.get_instrument_by(
        id_type=InstrumentIdType.INSTRUMENT_ID_TYPE_FIGI,
        id=figi,
    )
    inst = resp.instrument
    if inst is None:
        raise ValueError(f"Instrument not found for FIGI {figi}")

    # Пытаемся взять номинал/НКД (есть не во всех версиях/для всех инструментов)
    nominal = dec(getattr(inst, "nominal", None), "0")
    aci_value = dec(getattr(inst, "aci_value", None), "0")

    return {
        "name": inst.name,
        "ticker": inst.ticker,
        "lot": inst.lot,
        "currency": inst.currency,  # 'rub'
        "min_price_increment": dec(inst.min_price_increment, "0.01"),
        "uid": inst.uid,
        "type": str(
            getattr(inst, "instrument_type", "")
        ).lower(),  # 'bond' | 'share' | 'etf' ...
        "nominal": nominal,  # 0 если поле отсутствует
        "aci_value": aci_value,  # 0 если поле отсутствует
    }


def fetch_last_prices(client: Any, figis: List[str]) -> Dict[str, Decimal]:
    prices: Dict[str, Decimal] = {}
    resp = client.market_data.get_last_prices(figi=figis)
    for p in resp.last_prices:
        prices[p.figi] = dec(p.price)
    missing = [f for f in figis if f not in prices]
    if missing:
        raise RuntimeError(f"Missing last price for: {missing}")
    return prices


def get_available_rub(client: Any, account_id: str) -> Decimal:
    """Доступные RUB = свободные - заблокированные."""
    pos = client.operations.get_positions(account_id=account_id)
    rub_money = next((m for m in pos.money if m.currency.lower() == "rub"), None)
    rub_blocked = next((m for m in pos.blocked if m.currency.lower() == "rub"), None)
    free_rub = dec(rub_money) if rub_money else Decimal(0)
    blocked_rub = dec(rub_blocked) if rub_blocked else Decimal(0)
    return free_rub - blocked_rub


# ----------------------------- sizing -----------------------------
def floor_lots(budget_rub: Decimal, eff_lot_cost: Decimal) -> int:
    """Сколько лотов помещается в бюджет."""
    if eff_lot_cost <= 0:
        return 0
    lots = (budget_rub / eff_lot_cost).to_integral_value(rounding=ROUND_DOWN)
    return int(max(lots, 0))


# ----------------------------- main -----------------------------
def main():
    parser = argparse.ArgumentParser(
        description="DCA bot for Tinkoff Invest (MOEX, real account only, .env-based)"
    )
    parser.add_argument("--config", required=True, help="Path to YAML config")
    parser.add_argument(
        "--rub-budget",
        type=Decimal,
        required=True,
        help="Total RUB budget for this run, e.g. 10000",
    )
    parser.add_argument(
        "--day-of-month",
        type=int,
        default=5,
        help=(
            "Day of month to execute DCA (1..28). "
            "If falls on weekend and --allow-weekend is not set, trades move to the next Monday."
        ),
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Simulation only, no real orders"
    )
    parser.add_argument(
        "--fee-buf-bps",
        type=int,
        default=300,
        help="Buffer in bps for fees/slippage/rounding. Default 300 (3.00%)",
    )
    parser.add_argument(
        "--safe-rub-pct",
        type=float,
        default=0.97,
        help="Use only N% of available RUB to avoid 30042. Default 0.97",
    )
    parser.add_argument(
        "--wait-tradable-sec",
        type=int,
        default=0,
        help="Wait up to N seconds until instrument becomes tradable (0 = no wait)",
    )
    parser.add_argument(
        "--poll-sec",
        type=int,
        default=10,
        help="Polling interval for trading status while waiting",
    )
    parser.add_argument(
        "--allow-weekend",
        action="store_true",
        help="Place orders on Saturday/Sunday as well (by default, weekend runs are deferred to next Monday)",
    )
    args = parser.parse_args()

    load_dotenv()
    token = os.environ.get("TINKOFF_TOKEN", "")
    if not token:
        print("ERROR: TINKOFF_TOKEN is not set in environment (.env)", file=sys.stderr)
        sys.exit(2)
    explicit_account = os.environ.get("TINKOFF_ACCOUNT_ID") or None

    cfg = load_config(args.config)
    fee_buf = Decimal(args.fee_buf_bps) / Decimal(10000)
    safe_mul = Decimal(str(args.safe_rub_pct))

    # ---------------- Daily scheduling ----------------
    # Бот запускается КАЖДЫЙ день. Он должен исполнить сделки:
    # - либо в запланированный день месяца (по умолчанию 5-е),
    # - либо в ближайший понедельник, если запланированный день выпал на выходной,
    # - иначе — завершить выполнение и зафиксировать в логах причину пропуска.
    today = dt.date.today()
    # Ограничим диапазон 1..28, чтобы не зависеть от длины месяца
    scheduled_dom = args.day_of_month
    if scheduled_dom < 1:
        scheduled_dom = 1
    if scheduled_dom > 28:
        scheduled_dom = 28
    scheduled_date = dt.date(today.year, today.month, scheduled_dom)

    # Рассчитать эффективную дату исполнения с учётом переноса на понедельник
    if scheduled_date.weekday() >= 5 and not args.allow_weekend:
        # 5=суббота, 6=воскресенье -> перенос на ближайший понедельник
        days_to_mon = (7 - scheduled_date.weekday()) % 7
        if days_to_mon == 0:
            days_to_mon = 1
        effective_date = scheduled_date + dt.timedelta(days=days_to_mon)
    else:
        effective_date = scheduled_date

    period_key = dt.datetime.now().strftime("%Y-%m")

    # Если сегодня — запланированный день, но это выходной и торговать нельзя — фиксируем перенос
    if (
        today == scheduled_date
        and scheduled_date.weekday() >= 5
        and not args.allow_weekend
    ):
        insert_report_separator()
        for asset in cfg["assets"]:
            append_log(
                period_key=period_key,
                figi=asset.get("figi", ""),
                ticker="",
                name=asset.get("name", ""),
                instrument_type="",
                lots=0,
                lot_size=0,
                filled_price_per_share=Decimal(0),
                cost_rub=Decimal(0),
                status=f"DEFERRED_TO_MONDAY({effective_date.isoformat()})",
                order_request_id="",
            )
        print(
            f"Scheduled day is weekend (today={today.isoformat()}). Trades deferred to Monday {effective_date.isoformat()}.",
            file=sys.stderr,
        )
        return

    # Если сегодня не эффективная дата — просто фиксируем пропуск и выходим
    if today != effective_date:
        insert_report_separator()
        # Сводная запись (по всем инструментам) — чтобы не раздувать CSV
        append_log(
            period_key=period_key,
            figi="",
            ticker="",
            name="",
            instrument_type="",
            lots=0,
            lot_size=0,
            filled_price_per_share=Decimal(0),
            cost_rub=Decimal(0),
            status=f"SKIPPED_NOT_SCHEDULED_TODAY(expected={effective_date.isoformat()})",
            order_request_id="",
        )
        print(
            f"Not scheduled today (today={today.isoformat()}, expected={effective_date.isoformat()}).",
            file=sys.stderr,
        )
        return

    with Client(token) as client:
        account_id = pick_account_id(client, explicit_account)

        figis = [a["figi"] for a in cfg["assets"]]
        prices = fetch_last_prices(client, figis)
        metas = {
            a["figi"]: get_instrument_meta(client, a["figi"]) for a in cfg["assets"]
        }

        period_key = dt.datetime.now().strftime("%Y-%m")

        insert_report_separator()

        for asset in cfg["assets"]:
            figi = asset["figi"]
            weight = Decimal(str(asset["weight"]))
            alloc = (args.rub_budget * weight).quantize(Decimal("0.01"))
            meta = metas[figi]
            price_last = prices[figi]  # Quotation -> Decimal
            lot = meta["lot"]
            uid = meta["uid"]
            inst_type = meta["type"]  # 'bond'/'share'/'etf'/...

            # Пропуск не-RUB
            if str(meta["currency"]).lower() != "rub":
                append_log(
                    period_key=period_key,
                    figi=figi,
                    ticker=meta["ticker"],
                    name=meta["name"],
                    instrument_type=inst_type,
                    lots=0,
                    lot_size=lot,
                    filled_price_per_share=price_last,
                    cost_rub=Decimal(0),
                    status=f"SKIPPED_NON_RUB_CURRENCY({meta['currency']})",
                    order_request_id="",
                )
                continue

            # --- Корректная цена за 1 бумагу ---
            # Акции/ETF: last_price уже в ₽.
            # Облигации: last_price — это % от номинала. Берём (price% * nominal) + НКД.
            if inst_type == "bond":
                nominal = meta.get("nominal") or Decimal("0")
                if nominal <= 0:
                    nominal = Decimal("1000")  # безопасный дефолт
                aci = meta.get("aci_value") or Decimal("0")
                clean_rub = (price_last / Decimal("100")) * nominal
                price_per_share_effective = clean_rub + aci
            else:
                price_per_share_effective = price_last

            # Полная стоимость 1 лота + буфер
            eff_lot_cost = (
                price_per_share_effective * Decimal(lot) * (Decimal(1) + fee_buf)
            ).quantize(Decimal("0.01"))

            # Первый расчёт лотов от аллокации
            lots = floor_lots(alloc, eff_lot_cost)
            if lots <= 0:
                append_log(
                    period_key=period_key,
                    figi=figi,
                    ticker=meta["ticker"],
                    name=meta["name"],
                    instrument_type=inst_type,
                    lots=0,
                    lot_size=lot,
                    filled_price_per_share=price_per_share_effective,
                    cost_rub=Decimal(0),
                    status="SKIPPED_SMALL_ALLOC",
                    order_request_id="",
                )
                continue

            # Проверим статус торгуемости; по желанию — подождём
            def read_status():
                st = client.market_data.get_trading_status(instrument_id=uid)
                return (
                    bool(getattr(st, "api_trade_available_flag", False)),
                    bool(getattr(st, "market_order_available_flag", False)),
                    bool(getattr(st, "limit_order_available_flag", False)),
                    st.trading_status,
                )

            api_ok, mkt_ok, lim_ok, tr_stat = read_status()
            waited = 0
            while (
                args.wait_tradable_sec > 0
                and api_ok
                and (not mkt_ok and not lim_ok)
                and waited < args.wait_tradable_sec
            ):
                time.sleep(max(1, int(args.poll_sec)))
                waited += max(1, int(args.poll_sec))
                api_ok, mkt_ok, lim_ok, tr_stat = read_status()

            if not api_ok or (not mkt_ok and not lim_ok):
                append_log(
                    period_key=period_key,
                    figi=figi,
                    ticker=meta["ticker"],
                    name=meta["name"],
                    instrument_type=inst_type,
                    lots=0,
                    lot_size=lot,
                    filled_price_per_share=price_per_share_effective,
                    cost_rub=Decimal(0),
                    status=(
                        f"SKIPPED_TRADING_UNAVAILABLE(api={api_ok},mkt={mkt_ok},"
                        f"lim={lim_ok},status={tr_stat},waited_s={waited})"
                    ),
                    order_request_id="",
                )
                continue

            order_type = (
                OrderType.ORDER_TYPE_MARKET
                if mkt_ok
                else OrderType.ORDER_TYPE_BESTPRICE
            )

            # Учтём реальный доступный кэш и добавим safety‑множитель
            rub_available = get_available_rub(client, account_id)
            safe_rub = (rub_available * safe_mul).quantize(Decimal("0.01"))
            target_budget = min(alloc, safe_rub)
            lots = min(lots, floor_lots(target_budget, eff_lot_cost))

            if lots <= 0:
                append_log(
                    period_key=period_key,
                    figi=figi,
                    ticker=meta["ticker"],
                    name=meta["name"],
                    instrument_type=inst_type,
                    lots=0,
                    lot_size=lot,
                    filled_price_per_share=price_per_share_effective,
                    cost_rub=Decimal(0),
                    status="SKIPPED_NOT_ENOUGH_RUB_AFTER_SAFE_CHECK",
                    order_request_id="",
                )
                continue

            order_request_id = str(uuid4())
            status = "DRY_RUN" if args.dry_run else "PENDING"
            filled_price = Decimal(0)
            cost_rub = Decimal(0)

            if not args.dry_run:
                # Авто‑даунсайз при 30042 (до 6 попыток)
                attempts = 0
                cur_lots = lots
                while cur_lots > 0 and attempts < 6:
                    try:
                        resp = client.orders.post_order(
                            instrument_id=uid,  # используем UID
                            quantity=cur_lots,  # КОЛ-ВО ЛОТОВ
                            account_id=account_id,
                            direction=OrderDirection.ORDER_DIRECTION_BUY,
                            order_type=order_type,
                            order_id=str(uuid4()),  # новый UUID на попытку
                        )
                        filled_price = dec(resp.executed_order_price)
                        cost_rub = dec(resp.total_order_amount)
                        status = str(resp.execution_report_status).replace(
                            "EXECUTION_REPORT_STATUS_", ""
                        )
                        break
                    except RequestError as e:
                        if e.details == "30042":  # Not enough assets for a margin trade
                            cur_lots -= 1
                            attempts += 1
                            continue
                        if e.details == "30079":
                            # Instrument is not available for trading
                            status = (
                                f"SKIPPED_TRADING_UNAVAILABLE(api={api_ok},mkt={mkt_ok},"
                                f"lim={lim_ok},status={tr_stat})"
                            )
                            cur_lots = 0
                            break
                        import traceback

                        traceback.print_exc()
                        status = f"ERROR:{e!r}"
                        break
                    except Exception as e:
                        import traceback

                        traceback.print_exc()
                        status = f"ERROR:{e!r}"
                        break

                if status.startswith("PENDING") and cur_lots != lots:
                    status = f"DOWNSIZED_FROM_{lots}_TO_{cur_lots}"
                if (
                    (cur_lots <= 0)
                    and not status.startswith("ERROR")
                    and "SKIPPED_TRADING_UNAVAILABLE" not in status
                ):
                    status = "SKIPPED_NOT_ENOUGH_RUB_RUNTIME"

                lots_to_log = cur_lots
            else:
                lots_to_log = lots

            append_log(
                period_key=period_key,
                figi=figi,
                ticker=meta["ticker"],
                name=meta["name"],
                instrument_type=inst_type,
                lots=lots_to_log,
                lot_size=lot,
                filled_price_per_share=filled_price or price_per_share_effective,
                cost_rub=cost_rub,
                status=status,
                order_request_id=order_request_id if not args.dry_run else "",
            )

            time.sleep(0.2)  # бережно к rate limit

    print("Done. See orders_log.csv")


if __name__ == "__main__":
    main()
