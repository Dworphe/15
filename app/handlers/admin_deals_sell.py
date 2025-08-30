# app/handlers/admin_deals_sell.py
from __future__ import annotations
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import select
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import json

from app.config import settings
from app.db.base import async_session
from app.db.models import RoleEnum, User, Deal, DealType, DealStatus
from app.handlers.utils import ensure_user
from app.services.settings import get_settings
from app.utils.money import bank_round_2
from app.utils.validation import validate_decimal_range_step
from app.scheduler import schedule_stage1_end

router = Router(name="admin_deals_sell")
TZ = ZoneInfo("Europe/Amsterdam")

class SellStates(StatesGroup):
    amount_rub = State()
    base_pct = State()
    service_fee = State()
    audience = State()
    audience_params = State()
    pay_time = State()
    comment_ask = State()
    comment_text = State()
    warning_toggle = State()
    confirm = State()

def kb_yesno(cb_yes: str, cb_no: str):
    b = InlineKeyboardBuilder()
    b.button(text="Да", callback_data=cb_yes)
    b.button(text="Нет", callback_data=cb_no)
    b.adjust(2); return b.as_markup()

def kb_audience():
    b = InlineKeyboardBuilder()
    b.button(text="Для всех (онлайн + USDT маржа + карта)", callback_data="aud:all")
    b.button(text="По рейтингу", callback_data="aud:rep")
    b.button(text="Персонально", callback_data="aud:personal")
    b.adjust(1)
    return b.as_markup()

@router.message(Command("sell"))
async def sell_menu(message: Message, state: FSMContext):
    async with async_session() as s:
        u = await ensure_user(s, message.from_user)
        if u.role != RoleEnum.admin:
            await message.answer("Доступно только администратору.")
            return
    await state.clear()
    await state.set_state(SellStates.amount_rub)
    await message.answer("1) Введите сумму сделки в RUB:")

@router.message(SellStates.amount_rub)
async def step_amount(message: Message, state: FSMContext):
    try:
        rub = float((message.text or "").replace(" ", "").replace(",", "."))
        if rub <= 0: raise ValueError
        s = await get_settings()
        usdt = bank_round_2(rub / float(s.rub_per_usdt))
        await state.update_data(amount_rub=bank_round_2(rub), amount_usdt=usdt)
        await state.set_state(SellStates.base_pct)
        await message.answer(f"USDT эквивалент: ~ {usdt:.2f}\n2) Вознаграждение трейдера, % (−5.00..+25.00):")
    except Exception:
        await message.answer("Некорректная сумма. Повторите ввод.")

@router.message(SellStates.base_pct)
async def step_base_pct(message: Message, state: FSMContext):
    try:
        v = float((message.text or "").replace(",", "."))
        if not validate_decimal_range_step(v, -5.00, 25.00, 0.01): raise ValueError
        await state.update_data(base_pct=round(v,2))
        s = await get_settings()
        if s.service_fee_overridable:
            await state.set_state(SellStates.service_fee)
            await message.answer(f"3) Комиссия сервиса, % (диапазон {s.service_fee_min_pct:.2f}..{s.service_fee_max_pct:.2f}), шаг 0.01.\nПо умолчанию {s.service_fee_default_pct:.2f}%")
        else:
            await state.update_data(service_fee_pct=float(s.service_fee_default_pct))
            await state.set_state(SellStates.audience)
            await message.answer("4) Выберите аудиторию:", reply_markup=kb_audience())
    except Exception:
        await message.answer("Неверное значение. Введите в диапазоне −5.00..+25.00 с шагом 0.01.")

@router.message(SellStates.service_fee)
async def step_service_fee(message: Message, state: FSMContext):
    from app.services.settings import get_settings
    s = await get_settings()
    try:
        v = float((message.text or "").replace(",", "."))
        if not validate_decimal_range_step(v, float(s.service_fee_min_pct), float(s.service_fee_max_pct), 0.01): raise ValueError
        await state.update_data(service_fee_pct=round(v,2))
        await state.set_state(SellStates.audience)
        await message.answer("4) Выберите аудиторию:", reply_markup=kb_audience())
    except Exception:
        await message.answer(f"Неверно. Введите значение в диапазоне {float(s.service_fee_min_pct):.2f}..{float(s.service_fee_max_pct):.2f} с шагом 0.01.")

@router.callback_query(SellStates.audience, F.data.startswith("aud:"))
async def step_audience(cq: CallbackQuery, state: FSMContext):
    mode = cq.data.split(":",1)[1]
    # БЫЛО: 'per'
    if mode == "per": mode = "personal"
    await state.update_data(audience_type=mode)
    if mode == "rep":
        await state.set_state(SellStates.audience_params)
        await cq.message.answer("Укажите REP диапазон: 'от до' (можно оставить пустым один конец). Пример: '10 100' или '10' или ' 100'")
    elif mode == "personal":
        await state.set_state(SellStates.audience_params)
        await cq.message.answer("Укажите telegram_id трейдера:")
    else:
        await state.set_state(SellStates.pay_time)
        await cq.message.answer("5) Время на сделку (мин): 5/10/15/20/25/30")
    await cq.answer()

@router.message(SellStates.audience_params)
async def step_audience_params(message: Message, state: FSMContext):
    data = (message.text or "").strip()
    aud = (await state.get_data())["audience_type"]
    filt = {}
    if aud == "rep":
        parts = data.split()
        if len(parts) == 2:
            filt = {"rep_min": int(parts[0]), "rep_max": int(parts[1])}
        elif len(parts) == 1:
            try:
                v = int(parts[0]); filt = {"rep_min": v}
            except: filt = {"rep_max": int(parts[0])}
        else:
            filt = {}
    elif aud == "personal":
        try:
            filt = {"telegram_id": int(data)}
        except:
            await message.answer("Неверный telegram_id.")
            return
    await state.update_data(audience_filter=filt)
    await state.set_state(SellStates.pay_time)
    await message.answer("5) Время на сделку (мин): 5/10/15/20/25/30")

@router.message(SellStates.pay_time)
async def step_pay_time(message: Message, state: FSMContext):
    try:
        mins = int((message.text or "").strip())
        if mins not in (5,10,15,20,25,30): raise ValueError
        await state.update_data(pay_mins=mins)
        await state.set_state(SellStates.comment_ask)
        kb = InlineKeyboardBuilder()
        kb.button(text="Да", callback_data="cmt:yes")
        kb.button(text="Нет", callback_data="cmt:no")
        kb.adjust(2)
        await message.answer("6) Комментарий к сделке? (Да/Нет)", reply_markup=kb.as_markup())
    except Exception:
        await message.answer("Введите одно из значений: 5/10/15/20/25/30.")

@router.callback_query(SellStates.comment_ask, F.data.startswith("cmt:"))
async def step_comment_ask(cq: CallbackQuery, state: FSMContext):
    yn = cq.data.split(":",1)[1]
    if yn == "yes":
        await state.set_state(SellStates.comment_text)
        await cq.message.answer("Введите комментарий (до 1000 символов).")
    else:
        await state.update_data(comment=None)
        await state.set_state(SellStates.warning_toggle)
        kb = InlineKeyboardBuilder()
        kb.button(text="Да", callback_data="warn:yes")
        kb.button(text="Нет", callback_data="warn:no")
        kb.adjust(2)
        await cq.message.answer("7) Добавить предупреждение? (Да/Нет)", reply_markup=kb.as_markup())
    await cq.answer()

@router.message(SellStates.comment_text)
async def step_comment_text(message: Message, state: FSMContext):
    txt = (message.text or "").strip()
    if len(txt) > 1000:
        await message.answer("Слишком длинно. До 1000 символов.")
        return
    await state.update_data(comment=txt)
    await state.set_state(SellStates.warning_toggle)
    kb = InlineKeyboardBuilder()
    kb.button(text="Да", callback_data="warn:yes")
    kb.button(text="Нет", callback_data="warn:no")
    kb.adjust(2)
    await message.answer("7) Добавить предупреждение? (Да/Нет)", reply_markup=kb.as_markup())

@router.callback_query(SellStates.warning_toggle, F.data.startswith("warn:"))
async def step_warning(cq: CallbackQuery, state: FSMContext):
    yn = cq.data.split(":",1)[1]
    await state.update_data(warning_enabled=(yn=="yes"))
    data = await state.get_data()
    # подтверждение
    await state.set_state(SellStates.confirm)
    s = await get_settings()
    usdt = data["amount_usdt"]; rub = data["amount_rub"]
    txt = (
        "<b>Подтверждение сделки — ПРОДАЖА USDT</b>\n"
        f"Сумма: {rub:.2f} RUB (~{usdt:.2f} USDT)\n"
        f"Вознаграждение (база): {data['base_pct']:.2f}%\n"
        f"Комиссия сервиса: {data.get('service_fee_pct', float(s.service_fee_default_pct)):.2f}%\n"
        f"Аудитория: {data.get('audience_type')}\n"
        f"Комментарий: {data.get('comment') or '-'}\n"
        f"Предупреждение: {'вкл' if data.get('warning_enabled') else 'выкл'}\n"
        f"Время на сделку: {data['pay_mins']} мин"
    )
    b = InlineKeyboardBuilder()
    b.button(text="Опубликовать", callback_data="sell:publish")
    b.button(text="Отмена", callback_data="sell:cancel")
    b.adjust(2)
    await cq.message.answer(txt, reply_markup=b.as_markup())
    await cq.answer()

@router.callback_query(SellStates.confirm, F.data.startswith("sell:"))
async def publish_sell(cq: CallbackQuery, state: FSMContext):
    act = cq.data.split(":",1)[1]
    if act == "cancel":
        await state.clear()
        await cq.message.answer("Отменено.")
        await cq.answer(); return
    data = await state.get_data()
    s = await get_settings()

    # номер сделки
    from app.utils.dt import now_tz
    deal_no = now_tz().strftime("%Y%m%d-") + __import__("secrets").token_hex(4).upper()[:7]
    service_fee_pct = float(data.get("service_fee_pct", float(s.service_fee_default_pct)))

    async with async_session() as ss:
        u = await ensure_user(ss, cq.from_user)
        if u.role != RoleEnum.admin:
            await cq.answer("Только для администратора", show_alert=True); return

        deal = Deal(
            deal_no=deal_no,
            deal_type=DealType.SELL,
            amount_rub=float(data["amount_rub"]),
            amount_usdt_snapshot=float(data["amount_usdt"]),
            base_pct=float(data["base_pct"]),
            service_fee_pct=service_fee_pct,
            audience_type=data["audience_type"],
            audience_filter=json.dumps(data.get("audience_filter") or {}),
            comment=data.get("comment"),
            warning_enabled=bool(data.get("warning_enabled", False)),
            round_secs=int(s.round_secs_default),
            max_tie_rounds=int(s.max_tie_rounds_default),
            status=DealStatus.OPEN,
        )
        ss.add(deal)
        await ss.commit()

    await state.clear()
    await cq.message.answer(f"Сделка опубликована: ПРОДАЖА USDT\n№ <code>{deal_no}</code>\n"
                            f"{data['amount_rub']:.2f} RUB (~{data['amount_usdt']:.2f} USDT)\n"
                            f"База: {data['base_pct']:.2f}% • Комиссия сервиса: {service_fee_pct:.2f}%\n"
                            f"Время на сделку: {data['pay_mins']} мин")

    # Рассылка Этапа 1 + постановка завершения Этапа 1
    from app.handlers.sell_auction import broadcast_stage1
    from app.config import settings
    await broadcast_stage1(int(deal.id))
    await schedule_stage1_end(int(deal.id), after_seconds=int(s.round_secs_default), db_url=settings.database_url)
    await cq.answer()
