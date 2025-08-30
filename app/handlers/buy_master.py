from __future__ import annotations
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
from zoneinfo import ZoneInfo
from datetime import datetime
from sqlalchemy import select
import json

from app.db.base import async_session
from app.db.models import RoleEnum, User, Deal, DealType, DealStatus
from app.handlers.utils import ensure_user
from app.services.settings import get_settings
from app.utils.money import bank_round_2
from app.utils.validation import validate_decimal_range_step
from app.scheduler import schedule_stage1_end

router = Router(name="buy_master")
TZ = ZoneInfo("Europe/Amsterdam")


class BuyStates(StatesGroup):
    amount_rub = State()
    pay_account = State()
    base_pct = State()
    service_fee = State()
    audience = State()
    audience_params = State()
    pay_time = State()
    email_toggle = State()
    email_value = State()
    comment_toggle = State()
    comment_text = State()
    warning_toggle = State()
    confirm = State()


def kb_yesno(cb_yes: str, cb_no: str):
    b = InlineKeyboardBuilder()
    b.button(text="Да", callback_data=cb_yes)
    b.button(text="Нет", callback_data=cb_no)
    b.adjust(2)
    return b.as_markup()


def kb_audience():
    b = InlineKeyboardBuilder()
    b.button(text="Для всех (онлайн)", callback_data="aud:all")
    b.button(text="По рейтингу", callback_data="aud:rep")
    b.button(text="Персонально", callback_data="aud:personal")
    b.adjust(1)
    return b.as_markup()


@router.message(Command("buy"))
async def buy_cmd(message: Message, state: FSMContext):
    async with async_session() as s:
        u = await ensure_user(s, message.from_user)
        if u.role != RoleEnum.admin:
            await message.answer("Доступно только администратору.")
            return
    await state.clear()
    await state.set_state(BuyStates.amount_rub)
    await message.answer("1) Введите сумму сделки в RUB:")


@router.message(BuyStates.amount_rub)
async def step_amount(message: Message, state: FSMContext):
    try:
        rub = float((message.text or "").replace(" ", "").replace(",", "."))
        if rub <= 0:
            raise ValueError
        s = await get_settings()
        usdt = bank_round_2(rub / float(s.rub_per_usdt))
        await state.update_data(amount_rub=bank_round_2(rub), amount_usdt=usdt)
        await state.set_state(BuyStates.pay_account)
        await message.answer(
            f"USDT эквивалент: ~ {usdt:.2f}\n2) Введите счёт для перевода (напр. '1111000011110000 ТБАНК' или '+79991112233 СБЕРБАНК'):"
        )
    except Exception:
        await message.answer("Некорректная сумма. Повторите ввод.")


@router.message(BuyStates.pay_account)
async def step_account(message: Message, state: FSMContext):
    acc = (message.text or "").strip()
    if len(acc) < 6 or len(acc) > 64:
        await message.answer("Счёт должен быть длиной 6..64 символов.")
        return
    await state.update_data(pay_account=acc)
    await state.set_state(BuyStates.base_pct)
    await message.answer("3) Вознаграждение трейдера, % (−5.00..+25.00, шаг 0.01):")


@router.message(BuyStates.base_pct)
async def step_base(message: Message, state: FSMContext):
    try:
        v = float((message.text or "").replace(",", "."))
        if not validate_decimal_range_step(v, -5.00, 25.00, 0.01):
            raise ValueError
        await state.update_data(base_pct=round(v, 2))
        s = await get_settings()
        await state.set_state(BuyStates.service_fee)
        await message.answer(
            f"4) Комиссия сервиса, % ({float(s.service_fee_min_pct):.2f}..{float(s.service_fee_max_pct):.2f}, шаг 0.01). По умолчанию {float(s.service_fee_default_pct):.2f}%"
        )
    except Exception:
        await message.answer("Неверно. Введите число в диапазоне −5.00..+25.00 с шагом 0.01.")


@router.message(BuyStates.service_fee)
async def step_fee(message: Message, state: FSMContext):
    s = await get_settings()
    try:
        v = float((message.text or "").replace(",", "."))
        if not validate_decimal_range_step(v, float(s.service_fee_min_pct), float(s.service_fee_max_pct), 0.01):
            raise ValueError
        await state.update_data(service_fee_pct=round(v, 2))
        await state.set_state(BuyStates.audience)
        await message.answer("5) Выберите аудиторию:", reply_markup=kb_audience())
    except Exception:
        await message.answer(
            f"Неверно. {float(s.service_fee_min_pct):.2f}..{float(s.service_fee_max_pct):.2f}, шаг 0.01."
        )


@router.callback_query(BuyStates.audience, F.data.startswith("aud:"))
async def step_audience(cq: CallbackQuery, state: FSMContext):
    mode = cq.data.split(":", 1)[1]
    await state.update_data(audience_type=mode)
    await state.set_state(BuyStates.audience_params)
    if mode == "rep":
        await cq.message.answer(
            "Укажите REP диапазон 'от до' (можно один конец). Пример: '10 100' или '10' или ' 100'"
        )
    elif mode == "personal":
        await cq.message.answer("Укажите telegram_id трейдера:")
    else:
        await state.update_data(audience_filter={})
        await state.set_state(BuyStates.pay_time)
        await cq.message.answer("6) Время на оплату (мин): 5/10/15/20/25/30")
    await cq.answer()


@router.message(BuyStates.audience_params)
async def step_audience_params(message: Message, state: FSMContext):
    t = (await state.get_data()).get("audience_type")
    data = (message.text or "").strip()
    filt = {}
    try:
        if t == "rep":
            parts = data.split()
            if len(parts) == 2:
                filt = {"rep_min": int(parts[0]), "rep_max": int(parts[1])}
            elif len(parts) == 1:
                try:
                    filt = {"rep_min": int(parts[0])}
                except Exception:
                    filt = {"rep_max": int(parts[0])}
        elif t == "personal":
            filt = {"telegram_id": int(data)}
    except Exception:
        await message.answer("Неверный формат.")
        return
    await state.update_data(audience_filter=filt)
    await state.set_state(BuyStates.pay_time)
    await message.answer("6) Время на оплату (мин): 5/10/15/20/25/30")


@router.message(BuyStates.pay_time)
async def step_pay_time(message: Message, state: FSMContext):
    try:
        mins = int((message.text or "").strip())
        if mins not in (5, 10, 15, 20, 25, 30):
            raise ValueError
        await state.update_data(pay_mins=mins)
        await state.set_state(BuyStates.email_toggle)
        await message.answer(
            "7) Нужен e-mail для чека от банка? (Да/Нет)", reply_markup=kb_yesno("em:yes", "em:no")
        )
    except Exception:
        await message.answer("Введите одно из значений: 5/10/15/20/25/30.")


@router.callback_query(BuyStates.email_toggle, F.data.startswith("em:"))
async def step_email_toggle(cq: CallbackQuery, state: FSMContext):
    yn = cq.data.split(":", 1)[1]
    if yn == "yes":
        await state.set_state(BuyStates.email_value)
        await cq.message.answer("Введите e-mail (до 254 символов):")
    else:
        await state.update_data(email=None)
        await state.set_state(BuyStates.comment_toggle)
        await cq.message.answer(
            "8) Комментарий к сделке? (Да/Нет)", reply_markup=kb_yesno("cmt:yes", "cmt:no")
        )
    await cq.answer()


@router.message(BuyStates.email_value)
async def step_email_val(message: Message, state: FSMContext):
    email = (message.text or "").strip()
    if len(email) < 3 or len(email) > 254 or "@" not in email:
        await message.answer("Неверный e-mail.")
        return
    await state.update_data(email=email)
    await state.set_state(BuyStates.comment_toggle)
    await message.answer(
        "8) Комментарий к сделке? (Да/Нет)", reply_markup=kb_yesno("cmt:yes", "cmt:no")
    )


@router.callback_query(BuyStates.comment_toggle, F.data.startswith("cmt:"))
async def step_cmt_toggle(cq: CallbackQuery, state: FSMContext):
    yn = cq.data.split(":", 1)[1]
    if yn == "yes":
        await state.set_state(BuyStates.comment_text)
        await cq.message.answer("Введите комментарий (до 1000 символов).")
    else:
        await state.update_data(comment=None)
        await state.set_state(BuyStates.warning_toggle)
        await cq.message.answer(
            "9) Включить предупреждение? (Да/Нет)", reply_markup=kb_yesno("warn:yes", "warn:no")
        )
    await cq.answer()


@router.message(BuyStates.comment_text)
async def step_cmt_text(message: Message, state: FSMContext):
    txt = (message.text or "").strip()
    if len(txt) > 1000:
        await message.answer("До 1000 символов.")
        return
    await state.update_data(comment=txt)
    await state.set_state(BuyStates.warning_toggle)
    await message.answer(
        "9) Включить предупреждение? (Да/Нет)", reply_markup=kb_yesno("warn:yes", "warn:no")
    )


@router.callback_query(BuyStates.warning_toggle, F.data.startswith("warn:"))
async def step_warn(cq: CallbackQuery, state: FSMContext):
    yn = cq.data.split(":", 1)[1]
    await state.update_data(warning_enabled=(yn == "yes"))
    data = await state.get_data()
    txt = (
        "<b>Подтверждение сделки — ПОКУПКА</b>\n"
        f"Сумма: {data['amount_rub']:.2f} RUB (~{data['amount_usdt']:.2f} USDT)\n"
        f"Счёт: {data['pay_account']}\n"
        f"Вознаграждение (база): {data['base_pct']:.2f}%\n"
        f"Комиссия сервиса: {data['service_fee_pct']:.2f}%\n"
        f"Аудитория: {data['audience_type']}\n"
        f"Время на оплату: {data['pay_mins']} мин\n"
        f"E-mail: {data.get('email') or '-'}\n"
        f"Комментарий: {data.get('comment') or '-'}\n"
        f"Предупреждение: {'вкл' if data.get('warning_enabled') else 'выкл'}"
    )
    kb = InlineKeyboardBuilder()
    kb.button(text="Опубликовать", callback_data="buy:publish")
    kb.button(text="Отмена", callback_data="buy:cancel")
    kb.adjust(2)
    await state.set_state(BuyStates.confirm)
    await cq.message.answer(txt, reply_markup=kb.as_markup())
    await cq.answer()


@router.callback_query(BuyStates.confirm, F.data.startswith("buy:"))
async def buy_publish(cq: CallbackQuery, state: FSMContext):
    act = cq.data.split(":", 1)[1]
    if act == "cancel":
        await state.clear()
        await cq.message.answer("Отменено.")
        await cq.answer()
        return
    data = await state.get_data()
    s = await get_settings()
    from app.utils.dt import now_tz
    deal_no = now_tz().strftime("%Y%m%d-") + __import__("secrets").token_hex(4).upper()[:7]
    async with async_session() as ss:
        u = await ensure_user(ss, cq.from_user)
        if u.role != RoleEnum.admin:
            await cq.answer("Только админ.", show_alert=True)
            return
        deal = Deal(
            deal_no=deal_no,
            deal_type=DealType.BUY,
            amount_rub=float(data['amount_rub']),
            amount_usdt_snapshot=float(data['amount_usdt']),
            base_pct=float(data['base_pct']),
            service_fee_pct=float(data['service_fee_pct']),
            audience_type=data['audience_type'],
            audience_filter=json.dumps(data.get('audience_filter') or {}),
            comment=data.get('comment'),
            warning_enabled=bool(data.get('warning_enabled', False)),
            round_secs=int(s.round_secs_default),
            max_tie_rounds=int(s.max_tie_rounds_default),
            status=DealStatus.OPEN,
        )
        deal.winner_card_bank = None
        deal.winner_card_mask = data['pay_account']
        deal.winner_card_holder = data.get('email')
        deal.pay_mins = int(data['pay_mins'])
        ss.add(deal)
        await ss.commit()
    await state.clear()
    await cq.message.answer(
        f"Сделка опубликована: ПОКУПКА\n№ <code>{deal_no}</code>\n"
        f"{data['amount_rub']:.2f} RUB (~{data['amount_usdt']:.2f} USDT)\n"
        f"База: {data['base_pct']:.2f}% • Комиссия сервиса: {data['service_fee_pct']:.2f}%\n"
        f"Оплата в течение: {data['pay_mins']} мин"
    )
    from app.handlers.buy_auction import broadcast_stage1
    from app.config import settings
    await broadcast_stage1(int(deal.id))
    await schedule_stage1_end(int(deal.id), after_seconds=int(s.round_secs_default), db_url=settings.database_url)
    await cq.answer()


