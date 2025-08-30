from __future__ import annotations
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery, ReplyKeyboardRemove
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardMarkup, KeyboardButton
from sqlalchemy import select
import phonenumbers
from functools import wraps
import importlib

from app.config import settings
from app.db.base import async_session
from app.db.models import User, RoleEnum, TraderProfile, TraderCard, BankEnum, AccessToken
from app.handlers.utils import ensure_user, generate_token, mask_card

router = Router(name="admin")

def _try_import(path: str):
    try:
        return importlib.import_module(path)
    except Exception:
        return None

# Функция _resolve_buy_entry отключена в патче №20
# Мастер покупки/продажи временно недоступен

# ---- FSM ----
class RegStates(StatesGroup):
    ext_id = State()
    nickname = State()
    full_name = State()
    phone = State()
    referral = State()
    card_bank = State()
    card_bank_other = State()
    card_number = State()
    card_sbp_yesno = State()
    card_sbp_number = State()
    card_sbp_fio = State()
    card_holder = State()
    card_confirm = State()
    add_more_cards = State()
    finish_confirm = State()

BANK_LABELS = [
    BankEnum.TBANK, BankEnum.ALFABANK, BankEnum.SBERBANK, BankEnum.VTB,
    BankEnum.YANDEX, BankEnum.OZON, BankEnum.OTHER
]

def bank_kb():
    b = InlineKeyboardBuilder()
    for bank in BANK_LABELS:
        b.button(text=bank.value, callback_data=f"bank:{bank.name}")
    b.adjust(2)
    return b.as_markup()

def yesno_kb(cb_yes: str, cb_no: str):
    b = InlineKeyboardBuilder()
    b.button(text="Да", callback_data=cb_yes)
    b.button(text="Нет", callback_data=cb_no)
    b.adjust(2)
    return b.as_markup()

def admin_only(func):
    @wraps(func)
    async def wrapper(message: Message, *args, **kwargs):
        async with async_session() as session:
            u = await ensure_user(session, message.from_user)
            if u.role != RoleEnum.admin:
                await message.answer("Доступно только администратору.")
                return
        return await func(message, *args, **kwargs)
    return wrapper

@router.message(Command("admin"))
@admin_only
async def admin_menu(message: Message):
    b = InlineKeyboardBuilder()
    b.button(text="➕ Зарегистрировать трейдера", callback_data="admin:new_trader")
    # Новые торги (патч №21)
    b.button(text="🆕 Торги (NEW)", callback_data="admin:trading_menu")
    b.button(text="💰 Управление балансом", callback_data="admin:balance_menu")
    b.button(text="Настройки → Финансы", callback_data="admin:settings_fin")
    # NEW: кнопка «Курс USDT»
    b.button(text="💱 Курс USDT", callback_data="admin:rate_menu")
    b.adjust(1)
    await message.answer("Панель администратора", reply_markup=b.as_markup())

@router.callback_query(F.data == "admin:new_trader")
async def start_reg(cq: CallbackQuery, state: FSMContext):
    await state.clear()
    await state.set_state(RegStates.ext_id)
    await cq.message.answer("1) Введите ID трейдера (внешний идентификатор).")
    await cq.answer()

@router.callback_query(F.data == "admin:settings_fin")
async def nav_settings(cq: CallbackQuery):
    from app.handlers.admin_settings import show_finance_menu
    await show_finance_menu(cq.message, None)
    await cq.answer()

# Новые торги (патч №21)
@router.callback_query(F.data == "admin:trading_menu")
async def admin_trading_menu(cq: CallbackQuery):
    """Открытие меню новых торгов"""
    async with async_session() as s:
        u = await ensure_user(s, cq.from_user)
        if u.role != RoleEnum.admin:
            await cq.answer("Доступно только администратору.", show_alert=True)
            return
    
    b = InlineKeyboardBuilder()
    b.button(text="🔄 Создать Покупку (скелет)", callback_data="admin:trading_buy_new")
    b.button(text="🔄 Создать Продажу (скелет)", callback_data="admin:trading_sell_new")
    b.button(text="⬅️ Назад", callback_data="admin:menu")
    b.adjust(1)
    
    await cq.message.edit_text(
        "🆕 <b>Торги (NEW)</b>\n\n"
        "Выберите тип операции для создания:",
        reply_markup=b.as_markup()
    )
    await cq.answer()

# Обработчики торгов отключены в патче №20
# @router.callback_query(F.data == "admin:sell")
# @router.callback_query(F.data == "admin:buy_new")

@router.callback_query(F.data == "admin:balance_menu")
async def admin_open_balance_menu(cq: CallbackQuery):
    """Открытие меню управления балансом из основного меню админа"""
    from app.handlers.admin_balance import admin_balance_menu_kb
    await cq.message.answer(
        "<b>Операции с балансом</b>\nВыберите действие:",
        reply_markup=admin_balance_menu_kb()
    )
    await cq.answer()

@router.message(RegStates.ext_id)
async def reg_ext_id(message: Message, state: FSMContext):
    v = (message.text or "").strip()
    if not v:
        await message.answer("ID не может быть пустым. Повторите ввод.")
        return
    await state.update_data(ext_id=v)
    await state.set_state(RegStates.nickname)
    await message.answer("2) Никнейм трейдера (без @).")

@router.message(RegStates.nickname)
async def reg_nickname(message: Message, state: FSMContext):
    v = (message.text or "").strip().lstrip("@")
    if not v:
        await message.answer("Ник не может быть пустым. Повторите ввод.")
        return
    await state.update_data(nickname=v)
    await state.set_state(RegStates.full_name)
    await message.answer("3) Имя трейдера.")

@router.message(RegStates.full_name)
async def reg_full_name(message: Message, state: FSMContext):
    v = (message.text or "").strip()
    if not v:
        await message.answer("Имя не может быть пустым. Повторите ввод.")
        return
    await state.update_data(full_name=v)
    await state.set_state(RegStates.phone)
    await message.answer("4) Номер телефона трейдера (в любом формате, постараюсь распознать).")

@router.message(RegStates.phone)
async def reg_phone(message: Message, state: FSMContext):
    raw = (message.text or "").strip()
    try:
        num = phonenumbers.parse(raw, "RU")
        if not phonenumbers.is_valid_number(num):
            raise ValueError
        e164 = phonenumbers.format_number(num, phonenumbers.PhoneNumberFormat.E164)
    except Exception:
        await message.answer("Не удалось распознать номер. Введите корректный номер телефона.")
        return
    await state.update_data(phone=e164)
    await state.set_state(RegStates.referral)
    await message.answer("5) Реферальный код (можно оставить пустым, отправьте '-' если нет).")

@router.message(RegStates.referral)
async def reg_referral(message: Message, state: FSMContext):
    v = (message.text or "").strip()
    if v == "-" or v == "":
        v = None
    await state.update_data(referral=v)
    # Переходим к добавлению карты
    await state.update_data(cards=[])
    await state.set_state(RegStates.card_bank)
    await message.answer("6) Добавление карты трейдера.\nВыберите банк:", reply_markup=bank_kb())

@router.callback_query(RegStates.card_bank, F.data.startswith("bank:"))
async def card_pick_bank(cq: CallbackQuery, state: FSMContext):
    bank_name = cq.data.split(":", 1)[1]
    await cq.answer()
    if bank_name == BankEnum.OTHER.name:
        await state.set_state(RegStates.card_bank_other)
        await cq.message.answer("Введите название банка.")
    else:
        await state.update_data(card_bank=bank_name, card_bank_other=None)
        await state.set_state(RegStates.card_number)
        await cq.message.answer("Введите номер карты (16 цифр).")

@router.message(RegStates.card_bank_other)
async def card_bank_other(message: Message, state: FSMContext):
    name = (message.text or "").strip()
    if not name:
        await message.answer("Название не может быть пустым.")
        return
    await state.update_data(card_bank=BankEnum.OTHER.name, card_bank_other=name)
    await state.set_state(RegStates.card_number)
    await message.answer("Введите номер карты (16 цифр).")

@router.message(RegStates.card_number)
async def card_number(message: Message, state: FSMContext):
    digits = "".join(ch for ch in (message.text or "") if ch.isdigit())
    if len(digits) != 16:
        await message.answer("Нужно ровно 16 цифр. Повторите ввод.")
        return
    await state.update_data(card_number=digits)
    await state.set_state(RegStates.card_sbp_yesno)
    await message.answer("Есть ли СБП у карты?", reply_markup=yesno_kb("sbp:yes", "sbp:no"))

@router.callback_query(RegStates.card_sbp_yesno, F.data.startswith("sbp:"))
async def card_sbp(cq: CallbackQuery, state: FSMContext):
    ans = cq.data.split(":")[1]
    await cq.answer()
    if ans == "yes":
        await state.update_data(has_sbp=True)
        await state.set_state(RegStates.card_sbp_number)
        await cq.message.answer("Укажите номер для СБП.")
    else:
        await state.update_data(has_sbp=False, sbp_number=None, sbp_fullname=None)
        await state.set_state(RegStates.card_holder)
        await cq.message.answer("Имя держателя карты (как на карте).")

@router.message(RegStates.card_sbp_number)
async def card_sbp_number(message: Message, state: FSMContext):
    v = (message.text or "").strip()
    if not v:
        await message.answer("Номер СБП не может быть пустым.")
        return
    await state.update_data(sbp_number=v)
    await state.set_state(RegStates.card_sbp_fio)
    await message.answer("ФИО для СБП (как в банке).")

@router.message(RegStates.card_sbp_fio)
async def card_sbp_fio(message: Message, state: FSMContext):
    v = (message.text or "").strip()
    if not v:
        await message.answer("ФИО для СБП не может быть пустым.")
        return
    await state.update_data(sbp_fullname=v)
    await state.set_state(RegStates.card_holder)
    await message.answer("Имя держателя карты (как на карте).")

@router.message(RegStates.card_holder)
async def card_holder(message: Message, state: FSMContext):
    v = (message.text or "").strip()
    if not v:
        await message.answer("Имя держателя карты не может быть пустым.")
        return
    await state.update_data(holder_name=v)

    data = await state.get_data()
    bank = data["card_bank"]
    bank_other = data.get("card_bank_other")
    card_number = data["card_number"]
    has_sbp = data.get("has_sbp", False)
    sbp_number = data.get("sbp_number")
    sbp_fullname = data.get("sbp_fullname")

    text = (
        "<b>Проверьте данные карты</b>\n"
        f"Банк: {(bank_other if bank == BankEnum.OTHER.name else BankEnum[bank].value)}\n"
        f"Карта: {mask_card(card_number)}\n"
        f"Держатель: {v}\n"
        f"СБП: {'есть' if has_sbp else 'нет'}\n"
        f"{'СБП номер: ' + sbp_number if has_sbp else ''}\n"
        f"{'СБП ФИО: ' + sbp_fullname if has_sbp else ''}"
    )
    await state.set_state(RegStates.card_confirm)
    await message.answer(text, reply_markup=yesno_kb("card:ok", "card:redo"))

@router.callback_query(RegStates.card_confirm, F.data.startswith("card:"))
async def card_confirm(cq: CallbackQuery, state: FSMContext):
    action = cq.data.split(":")[1]
    await cq.answer()
    if action == "redo":
        await state.set_state(RegStates.card_bank)
        await cq.message.answer("Повторим ввод карты. Выберите банк:", reply_markup=bank_kb())
        return

    data = await state.get_data()
    cards = list(data.get("cards", []))
    cards.append({
        "bank": data["card_bank"],
        "bank_other_name": data.get("card_bank_other"),
        "card_number": data["card_number"],
        "holder_name": data["holder_name"],
        "has_sbp": data.get("has_sbp", False),
        "sbp_number": data.get("sbp_number"),
        "sbp_fullname": data.get("sbp_fullname"),
    })
    await state.update_data(cards=cards)

    await state.set_state(RegStates.add_more_cards)
    await cq.message.answer("Добавить ещё карту?", reply_markup=yesno_kb("more:yes", "more:no"))

@router.callback_query(RegStates.add_more_cards, F.data.startswith("more:"))
async def more_cards(cq: CallbackQuery, state: FSMContext):
    ans = cq.data.split(":")[1]
    await cq.answer()
    if ans == "yes":
        # Сброс временных полей карты
        for k in ["card_bank","card_bank_other","card_number","holder_name","has_sbp","sbp_number","sbp_fullname"]:
            await state.update_data(**{k: None})
        await state.set_state(RegStates.card_bank)
        await cq.message.answer("Выберите банк следующей карты:", reply_markup=bank_kb())
        return

    # Иначе показываем сводку и подтверждение
    data = await state.get_data()
    summary = (
        "<b>Подтверждение регистрации трейдера</b>\n\n"
        f"ID: {data['ext_id']}\n"
        f"Ник: @{data['nickname']}\n"
        f"Имя: {data['full_name']}\n"
        f"Телефон: {data['phone']}\n"
        f"Реф. код: {data.get('referral') or '-'}\n"
        f"Карт: {len(data.get('cards', []))}\n"
    )
    await state.set_state(RegStates.finish_confirm)
    await cq.message.answer(summary, reply_markup=yesno_kb("finish:ok", "finish:cancel"))

@router.callback_query(RegStates.finish_confirm, F.data.startswith("finish:"))
async def finish(cq: CallbackQuery, state: FSMContext):
    ans = cq.data.split(":")[1]
    await cq.answer()
    if ans == "cancel":
        await state.clear()
        await cq.message.answer("Отменено.")
        return

    data = await state.get_data()
    ext_id = data["ext_id"]
    nickname = data["nickname"]
    full_name = data["full_name"]
    phone = data["phone"]
    referral = data.get("referral")
    cards = data.get("cards", [])

    # Создаём профиль + карты + токен
    async with async_session() as session:
        # Проверим уникальность external_id
        exists = await session.scalar(select(TraderProfile).where(TraderProfile.external_id == ext_id))
        if exists:
            await cq.message.answer("Профиль с таким ID уже существует. Процесс прерван.")
            return

        profile = TraderProfile(
            external_id=ext_id,
            nickname=nickname,
            full_name=full_name,
            phone=phone,
            referral_code=referral,
            user_id=None,
        )
        session.add(profile)
        await session.flush()

        for c in cards:
            bank_enum = BankEnum[c["bank"]] if c["bank"] in BankEnum.__members__ else BankEnum.OTHER
            session.add(TraderCard(
                profile_id=profile.id,
                bank=bank_enum,
                bank_other_name=c.get("bank_other_name"),
                card_number=c["card_number"],
                holder_name=c["holder_name"],
                has_sbp=bool(c.get("has_sbp")),
                sbp_number=c.get("sbp_number"),
                sbp_fullname=c.get("sbp_fullname"),
            ))

        # Токен на 20 символов, гарантируем уникальность
        token = generate_token(20)
        while await session.scalar(select(AccessToken).where(AccessToken.token == token)):
            token = generate_token(20)

        session.add(AccessToken(profile_id=profile.id, token=token))
        await session.commit()

    await state.clear()
    await cq.message.answer(
        "<b>Готово!</b>\n"
        "Профиль трейдера создан. Передайте трейдеру этот токен для доступа к боту:\n\n"
        f"<code>{token}</code>\n\n"
        "Трейдер вводит команду /access и вставляет токен."
    )
