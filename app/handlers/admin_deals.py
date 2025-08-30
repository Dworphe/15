from __future__ import annotations
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import select
from datetime import datetime
from zoneinfo import ZoneInfo

from app.db.base import async_session
from app.db.models import User, RoleEnum, Deal, DealStatus
from app.handlers.utils import ensure_user
from app.services.settings import get_settings
from app.utils.validation import validate_pay_account, validate_email_addr, validate_decimal_range_step
from app.utils.money import bank_round_2

router = Router(name="admin_deals")
TZ = ZoneInfo("Europe/Amsterdam")

class DealStates(StatesGroup):
    amount = State()
    pay_account = State()
    base_pct = State()
    service_fee = State()
    bank_pick = State()
    need_email = State()
    email_input = State()
    pay_time = State()
    comment_ask = State()
    comment_text = State()
    warning_toggle = State()
    audience = State()
    confirm = State()

BANKS = ["ТБАНК","СБЕРБАНК","АЛЬФАБАНК","ВТБ","ЯНДЕКС БАНК","ОЗОН БАНК","ЛЮБОЙ БАНК"]

def kb_banks():
    b = InlineKeyboardBuilder()
    for name in BANKS: b.button(text=name, callback_data=f"bank:{name}")
    b.adjust(2)
    return b.as_markup()

def kb_yesno(cb_yes: str, cb_no: str):
    b = InlineKeyboardBuilder()
    b.button(text="Да", callback_data=cb_yes)
    b.button(text="Нет", callback_data=cb_no)
    b.adjust(2)
    return b.as_markup()

@router.message(Command("deals"))
async def deals_menu(message: Message):
    async with async_session() as session:
        u = await ensure_user(session, message.from_user)
        if u.role != RoleEnum.admin:
            await message.answer("Доступно только администратору.")
            return
    kb = InlineKeyboardBuilder()
    kb.button(text="➕ Покупка (мастер)", callback_data="deal:new_buy")
    kb.adjust(1)
    await message.answer("Торги → Покупка", reply_markup=kb.as_markup())

@router.callback_query(F.data == "deal:new_buy")
async def start_buy(cq: CallbackQuery, state: FSMContext):
    await state.clear()
    await state.set_state(DealStates.amount)
    await cq.message.answer("1) Введите сумму сделки в RUB (например 150000).")
    await cq.answer()

@router.message(DealStates.amount)
async def step_amount(message: Message, state: FSMContext):
    try:
        rub = float((message.text or "").replace(" ", "").replace(",", "."))
        if rub <= 0: raise ValueError
        s = await get_settings()
        usdt = bank_round_2(rub / s.rub_per_usdt)
        await state.update_data(amount_rub=bank_round_2(rub), amount_usdt=usdt)
        await state.set_state(DealStates.pay_account)
        await message.answer(f"USDT эквивалент: ~ {usdt:.2f}\n2) Введите счёт перевода (пример: 11110000111100001111 ТБАНК или +7911111001100 ТБАНК).")
    except Exception:
        await message.answer("Некорректная сумма. Повторите ввод.")

@router.message(DealStates.pay_account)
async def step_pay_account(message: Message, state: FSMContext):
    s = (message.text or "").strip()
    if not validate_pay_account(s) or not (6 <= len(s) <= 64):
        await message.answer("Счёт некорректен. Разрешены цифры, '+', пробелы, длина 6..64. В конце укажите банк.")
        return
    await state.update_data(pay_account=s)
    await state.set_state(DealStates.base_pct)
    await message.answer("3) Введите базовое вознаграждение трейдера, % (−5.00 .. +25.00).")

@router.message(DealStates.base_pct)
async def step_base_pct(message: Message, state: FSMContext):
    try:
        v = float((message.text or "").replace(",", "."))
        if not validate_decimal_range_step(v, -5.00, 25.00, 0.01): raise ValueError
        await state.update_data(base_pct=round(v,2))
        s = await get_settings()
        if s.service_fee_overridable:
            await state.set_state(DealStates.service_fee)
            await message.answer(f"4) Комиссия сервиса, % (диапазон {s.service_fee_min_pct:.2f}..{s.service_fee_max_pct:.2f}). По умолчанию: {s.service_fee_default_pct:.2f}%")
        else:
            await state.update_data(service_fee_pct=s.service_fee_default_pct)
            await state.set_state(DealStates.bank_pick)
            await message.answer("6) Выберите банк для оплаты:", reply_markup=kb_banks())
    except Exception:
        await message.answer("Неверное значение. Введите в диапазоне −5.00..+25.00 с шагом 0.01.")

@router.message(DealStates.service_fee)
async def step_service_fee(message: Message, state: FSMContext):
    try:
        s = await get_settings()
        v = float((message.text or "").replace(",", "."))
        if not validate_decimal_range_step(v, s.service_fee_min_pct, s.service_fee_max_pct, 0.01): raise ValueError
        await state.update_data(service_fee_pct=round(v,2))
        await state.set_state(DealStates.bank_pick)
        await message.answer("6) Выберите банк для оплаты:", reply_markup=kb_banks())
    except Exception:
        s = await get_settings()
        await message.answer(f"Неверно. Введите значение в диапазоне {s.service_fee_min_pct:.2f}..{s.service_fee_max_pct:.2f} с шагом 0.01.")

@router.callback_query(DealStates.bank_pick, F.data.startswith("bank:"))
async def step_bank(cq: CallbackQuery, state: FSMContext):
    bank = cq.data.split(":",1)[1]
    await state.update_data(pay_bank=bank)
    await state.set_state(DealStates.need_email)
    await cq.message.answer("7) Нужно отправить чек на e-mail? (Да/Нет)", reply_markup=kb_yesno("email:yes","email:no"))
    await cq.answer()

@router.callback_query(DealStates.need_email, F.data.startswith("email:"))
async def step_need_email(cq: CallbackQuery, state: FSMContext):
    yn = cq.data.split(":",1)[1]
    if yn == "yes":
        await state.update_data(need_email=True)
        await state.set_state(DealStates.email_input)
        await cq.message.answer("Введите e-mail для чека.")
    else:
        await state.update_data(need_email=False, email_for_receipt=None)
        await state.set_state(DealStates.pay_time)
        await cq.message.answer("8) Время на оплату (мин): выберите 5/10/15/20/25/30")

@router.message(DealStates.email_input)
async def step_email(message: Message, state: FSMContext):
    addr = (message.text or "").strip()
    if not validate_email_addr(addr):
        await message.answer("E-mail некорректен. Повторите.")
        return
    await state.update_data(email_for_receipt=addr)
    await state.set_state(DealStates.pay_time)
    await message.answer("8) Время на оплату (мин): выберите 5/10/15/20/25/30")

@router.message(DealStates.pay_time)
async def step_pay_time(message: Message, state: FSMContext):
    try:
        mins = int((message.text or "").strip())
        if mins not in (5,10,15,20,25,30): raise ValueError
        await state.update_data(pay_mins=mins)
        await state.set_state(DealStates.comment_ask)
        await message.answer("9) Добавить комментарий? (Да/Нет)", reply_markup=kb_yesno("cmt:yes","cmt:no"))
    except Exception:
        await message.answer("Введите одно из значений: 5/10/15/20/25/30.")

@router.callback_query(DealStates.comment_ask, F.data.startswith("cmt:"))
async def step_comment_ask(cq: CallbackQuery, state: FSMContext):
    yn = cq.data.split(":",1)[1]
    if yn == "yes":
        await state.set_state(DealStates.comment_text)
        await cq.message.answer("Введите комментарий (до 1000 символов).")
    else:
        await state.update_data(comment=None)
        await state.set_state(DealStates.warning_toggle)
        await cq.message.answer("10) Добавить предупреждение? (Да/Нет)", reply_markup=kb_yesno("warn:yes","warn:no"))
    await cq.answer()

@router.message(DealStates.comment_text)
async def step_comment_text(message: Message, state: FSMContext):
    txt = (message.text or "").strip()
    if len(txt) > 1000:
        await message.answer("Слишком длинно. До 1000 символов.")
        return
    await state.update_data(comment=txt)
    await state.set_state(DealStates.warning_toggle)
    await message.answer("10) Добавить предупреждение? (Да/Нет)", reply_markup=kb_yesno("warn:yes","warn:no"))

@router.callback_query(DealStates.warning_toggle, F.data.startswith("warn:"))
async def step_warning(cq: CallbackQuery, state: FSMContext):
    yn = cq.data.split(":",1)[1]
    await state.update_data(warning_enabled=(yn=="yes"))
    # Аудитория: пока даём 3 варианта
    await state.set_state(DealStates.audience)
    kb = InlineKeyboardBuilder()
    kb.button(text="Для всех (онлайн)", callback_data="aud:all")
    kb.button(text="По рейтингу", callback_data="aud:rep")
    kb.button(text="Персонально", callback_data="aud:per")
    kb.adjust(1)
    await cq.message.answer("11) Выберите аудиторию:", reply_markup=kb.as_markup())
    await cq.answer()

@router.callback_query(DealStates.audience, F.data.startswith("aud:"))
async def step_audience(cq: CallbackQuery, state: FSMContext):
    mode = cq.data.split(":",1)[1]
    data = await state.get_data()
    s = await get_settings()

    from app.utils.dt import now_tz
    deal_no = now_tz().strftime("%Y%m%d-") + __import__("secrets").token_hex(4).upper()[:7]
    # СНЕПШОТ комиссии — из состояния (либо из дефолта, если переопределение выключено)
    service_fee_pct = float(data["service_fee_pct"]) if "service_fee_pct" in data else float((await get_settings()).service_fee_default_pct)

    async with async_session() as session:
        u = await ensure_user(session, cq.from_user)
        if u.role != RoleEnum.admin:
            await cq.answer("Только для администратора", show_alert=True)
            return

        deal = Deal(
            deal_no=deal_no,
            amount_rub=data["amount_rub"],
            amount_usdt_snapshot=data["amount_usdt"],
            base_pct=data["base_pct"],
            service_fee_pct=service_fee_pct,
            pay_bank=data["pay_bank"],
            pay_account=data["pay_account"],
            need_email=data["need_email"],
            email_for_receipt=data.get("email_for_receipt"),
            comment=data.get("comment"),
            warning_enabled=data.get("warning_enabled", False),
            audience_type={"all":"all","rep":"rep_range","per":"personal"}[mode],
            audience_filter=None,
            round_secs=s.round_secs_default,
            max_tie_rounds=s.max_tie_rounds_default,
            status=DealStatus.OPEN,
        )
        session.add(deal)
        await session.commit()

        # Запускаем Этап 1 после успешного создания сделки
        from app.services.buy_flow import start_stage1_for_deal
        await start_stage1_for_deal(deal.id)

    await state.clear()
    await cq.message.answer(
        "<b>Сделка опубликована</b>\n"
        f"№: <code>{deal_no}</code>\n"
        f"Сумма: {data['amount_rub']:.2f} RUB (~{data['amount_usdt']:.2f} USDT)\n"
        f"Базовое вознаграждение: {data['base_pct']:.2f}%\n"
        f"Комиссия сервиса (снэпшот): {service_fee_pct:.2f}%\n"
        f"Банк: {data['pay_bank']}\n"
        f"Счёт: {data['pay_account']}\n"
        f"Чек на e-mail: {'да' if data['need_email'] else 'нет'}\n"
        f"Комментарий: {data.get('comment') or '-'}\n"
        f"Предупреждение: {'вкл' if data.get('warning_enabled') else 'выкл'}\n"
        f"Аудитория: {mode}"
    )
    await cq.answer()
