# app/workers/auction.py
from __future__ import annotations
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from sqlalchemy import select, asc
from aiogram import Bot
from app.config import settings
from app.db.base import async_session
from app.db.models import Deal, DealStatus, DealRound, Bid, User, DealType
from app.services.balance import reserve_usdt, release_usdt, consume_reserved_usdt
from app.utils.money import bank_round_2
from app.utils.dt import TZ, now_tz, to_aware_utc
from app.scheduler import get_scheduler

def _resolve_notify_round_start(deal_type: DealType):
    """Возвращает функцию notify_round_start(deal_id: int, round_number: int) для нужного типа сделки."""
    if deal_type == DealType.SELL:
        from app.handlers.sell_auction import notify_round_start as _notify
        return _notify
    if deal_type == DealType.BUY:
        from app.handlers.buy_auction import notify_round_start as _notify
        return _notify
    # Попытки найти BUY-модуль
    for modpath in ("app.handlers.buy_auction", "app.handlers.auction_buy", "app.handlers.auction"):
        try:
            mod = __import__(modpath, fromlist=["notify_round_start"])
            return getattr(mod, "notify_round_start")
        except Exception:
            continue
    # --- ФОЛЛБЕК: простое уведомление для покупок ---
    async def _fallback(deal_id: int, round_number: int):
        from app.bot import create_bot_and_dp
        from sqlalchemy import select
        from app.db.base import async_session
        from app.db.models import Deal, DealParticipant, User
        bot, _ = create_bot_and_dp(settings.bot_token)
        async with async_session() as s:
            d = await s.get(Deal, deal_id)
            if not d:
                return
            rows = await s.execute(
                select(User.tg_id).join(DealParticipant, DealParticipant.user_id==User.id)
                .where(DealParticipant.deal_id==deal_id, DealParticipant.accepted==True)
            )
            hint = f"Введите вашу ставку в диапазоне [-5.00; {float(d.base_pct):.2f}] с шагом 0.01"
            for tg_id, in rows.all():
                try:
                    await bot.send_message(int(tg_id),
                        f"<b>АУКЦИОН НАЧАЛСЯ — РАУНД {round_number}</b>\n{hint}\n"
                        "Отправьте число (например 0.25 или -1.50)."
                    )
                except:  # noqa: E722
                    pass
    return _fallback

async def _get_bot() -> Bot:
    from app.bot import create_bot_and_dp
    bot, _ = create_bot_and_dp(settings.bot_token)
    return bot

# Импортируем новые функции из buy_flow
from app.services.buy_flow import end_stage1, end_round

# Функция end_round теперь импортируется из buy_flow.py

async def _after_winner_assigned(s_in: any = None, deal_id: int | None = None, deal: Deal | None = None):
    if not deal:
        async with async_session() as s:
            deal = await s.get(Deal, deal_id)
    if not deal: return
    # Резерв USDT
    ok = await reserve_usdt(int(deal.winner_user_id), float(deal.amount_usdt_snapshot))
    if not ok:
        async with async_session() as s:
            d = await s.get(Deal, deal.id)
            d.status = DealStatus.CANCELLED
            await s.commit()
        return
    # Попросить выбрать карту и запустить Этап A (дедлайн оплаты)
    from app.scheduler import schedule_pay_timeout
    pay_minutes = int(getattr(deal, "pay_mins", 20) or 20)
    deal.pay_deadline_at = now_tz() + timedelta(minutes=pay_minutes)
    async with async_session() as s:
        d = await s.get(Deal, deal.id)
        d.pay_deadline_at = deal.pay_deadline_at
        d.status = DealStatus.WAITING_ADMIN_TRANSFER
        await s.commit()
    bot = await _get_bot()
    await bot.send_message(
        int(await _winner_tg_id(deal.winner_user_id)),
        (
          "<b>Вы выиграли аукцион — ПРОДАЖА USDT</b>\n"
          f"Сделка № <code>{deal.deal_no}</code>\n"
          f"Сумма: {float(deal.amount_rub):.2f} RUB (~{float(deal.amount_usdt_snapshot):.2f} USDT)\n"
          f"Ваша ставка: {float(deal.winner_bid_pct):.2f}%\n\n"
          f"Выберите карту: /pick_card {deal.id}\n"
          f"Дедлайн оплаты админом: до {deal.pay_deadline_at.astimezone(TZ).strftime('%H:%M')}"
        )
    )
    from app.config import settings
    await schedule_pay_timeout(deal.id, after_minutes=pay_minutes, db_url=settings.database_url)

async def _after_winner_assigned_buy(deal_id: int):
    from app.bot import create_bot_and_dp
    from app.scheduler import schedule_buy_timeout
    async with async_session() as s:
        d = await s.get(Deal, deal_id)
        if not d:
            return
        d.status = DealStatus.WINNER_ASSIGNED
        mins = int(getattr(d, "pay_mins", 20) or 20)
        d.pay_deadline_at = now_tz() + timedelta(minutes=mins)
        await s.commit()
    bot, _ = create_bot_and_dp("")
    async with async_session() as s:
        d = await s.get(Deal, deal_id)
        await bot.send_message(
            int(await _winner_tg_id(d.winner_user_id)),
            (
                "<b>ВЫ ВЫИГРАЛИ АУКЦИОН</b>\nВид: ПОКУПКА\n"
                f"Сделка № <code>{d.deal_no}</code>\n"
                f"Ваше вознаграждение: {float(d.winner_bid_pct):.2f}%\n"
                f"Сумма: {float(d.amount_rub):.2f} RUB (~{float(d.amount_usdt_snapshot):.2f} USDT)\n"
                f"Переведите на реквизиты: {d.winner_card_mask}\nE-mail для чека: {d.winner_card_holder or '-'}\n"
                f"Дедлайн: до {d.pay_deadline_at.astimezone(TZ).strftime('%H:%M')}"
            ),
            reply_markup=__import__("app.handlers.buy_winner", fromlist=["winner_kb"]).winner_kb(d.id)
        )
    from app.config import settings
    await schedule_buy_timeout(d.id, after_minutes=mins, db_url=settings.database_url)

async def pay_timeout(deal_id: int):
    async with async_session() as s:
        d = await s.get(Deal, deal_id)
        if not d or d.status not in (DealStatus.WINNER_ASSIGNED, DealStatus.WAITING_ADMIN_TRANSFER):
            return
        if d.winner_user_id:
            await release_usdt(int(d.winner_user_id), float(d.amount_usdt_snapshot))
        d.status = DealStatus.CANCELLED_TIMEOUT
        await s.commit()

async def buy_pay_timeout(deal_id: int):
    async with async_session() as s:
        d = await s.get(Deal, deal_id)
        if not d or d.deal_type != DealType.BUY:
            return
        if d.winner_user_id:
            u = await s.get(User, int(d.winner_user_id))
            u.balance_rub = float(u.balance_rub) - float(d.amount_rub)
        d.status = DealStatus.CANCELLED_TIMEOUT
        await s.commit()

async def trader_confirm_timeout(deal_id: int):
    async with async_session() as s:
        d = await s.get(Deal, deal_id)
        if not d or d.status != DealStatus.WAITING_TRADER_CONFIRM:
            return
        d.status = DealStatus.REVIEW
        await s.commit()

async def _notify_winner_basic(deal: Deal):
    bot = await _get_bot()
    await bot.send_message(
        chat_id=int(await _winner_tg_id(deal.winner_user_id)),
        text=(
            "<b>Вы выиграли аукцион — ПРОДАЖА USDT</b>\n"
            f"Сделка № <code>{deal.deal_no}</code>\n"
            f"Сумма: {float(deal.amount_rub):.2f} RUB (~{float(deal.amount_usdt_snapshot):.2f} USDT)\n"
            f"Ваша ставка: {float(deal.winner_bid_pct):.2f}%\n\n"
            "Дальнейшие действия (выбор карты и подтверждения) будут доступны после установки ПАТЧА №3."
        )
    )

async def _winner_tg_id(user_id: int) -> int:
    async with async_session() as s:
        from app.db.models import User
        u = await s.get(User, user_id)
        return int(u.tg_id or 0)
