# Implement patch for new purchase functionality
_Exported on 8/28/2025 at 20:07:53 GMT+3 from Cursor (1.5.5)_

---

**User**

ПАТЧ №10 — удалить старую «Покупку» и поставить новую (изолированно от SELL)
0) Что делает патч

Удаляет старую кнопку/колбэк покупки из админ-меню.

Не трогает файлы продажи.

Добавляет новый мастер «Покупка (BUY)» и аукцион/финализацию в отдельных файлах.

Воркер выбирает нужные уведомители по DealType без влияния на SELL.

1) Админ-меню — убрать старую кнопку, добавить новую

Файл: app/handlers/admin.py

В функции, которая строит клавиатуру панели, удали старую кнопку (любую из этих строк, если есть):

# удалить, если есть
kb.button(text="Торги → Покупка (мастер)", callback_data="deal:new_buy")
kb.button(text="Торги → Покупка (мастер)", callback_data="admin:buy")


Добавь новую кнопку с отдельным колбэком:

kb.button(text="Торги → Покупка (мастер)", callback_data="admin:buy_new")


Добавь обработчик колбэка:

from aiogram import F
from aiogram.types import CallbackQuery
from aiogram.fsm.context import FSMContext
from app.db.base import async_session
from app.handlers.utils import ensure_user
from app.db.models import RoleEnum

@router.callback_query(F.data == "admin:buy_new")
async def admin_open_buy_new(cq: CallbackQuery, state: FSMContext):
    async with async_session() as s:
        u = await ensure_user(s, cq.from_user)
        if u.role != RoleEnum.admin:
            await cq.answer("Доступно только администратору.", show_alert=True)
            return
    from app.handlers.buy_master import BuyStates  # наш НОВЫЙ модуль
    await state.clear()
    await state.set_state(BuyStates.amount_rub)
    await cq.message.answer("1) Введите сумму сделки в RUB:")
    await cq.answer()


Если в файле остались старые обработчики admin:buy / deal:new_buy — удалите их, чтобы они не перехватывали клик.

2) Новый мастер «Покупка» (не трогает SELL)

Файл (НОВЫЙ): app/handlers/buy_master.py

from __future__ import annotations
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import select
from zoneinfo import ZoneInfo
from datetime import datetime
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
    b.adjust(2); return b.as_markup()

def kb_audience():
    b = InlineKeyboardBuilder()
    b.button(text="Для всех (онлайн)", callback_data="aud:all")
    b.button(text="По рейтингу", callback_data="aud:rep")
    b.button(text="Персонально", callback_data="aud:personal")
    b.adjust(1); return b.as_markup()

@router.message(Command("buy"))
async def buy_cmd(message: Message, state: FSMContext):
    async with async_session() as s:
        u = await ensure_user(s, message.from_user)
        if u.role != RoleEnum.admin:
            await message.answer("Доступно только администратору."); return
    await state.clear()
    await state.set_state(BuyStates.amount_rub)
    await message.answer("1) Введите сумму сделки в RUB:")

@router.message(BuyStates.amount_rub)
async def step_amount(message: Message, state: FSMContext):
    try:
        rub = float((message.text or "").replace(" ", "").replace(",", "."))
        if rub <= 0: raise ValueError
        s = await get_settings()
        usdt = bank_round_2(rub / float(s.rub_per_usdt))
        await state.update_data(amount_rub=bank_round_2(rub), amount_usdt=usdt)
        await state.set_state(BuyStates.pay_account)
        await message.answer(f"USDT эквивалент: ~ {usdt:.2f}\n2) Введите счёт для перевода (напр. '1111000011110000 ТБАНК' или '+79991112233 СБЕРБАНК'):")
    except Exception:
        await message.answer("Некорректная сумма. Повторите ввод.")

@router.message(BuyStates.pay_account)
async def step_account(message: Message, state: FSMContext):
    acc = (message.text or "").strip()
    if len(acc) < 6 or len(acc) > 64:
        await message.answer("Счёт должен быть длиной 6..64 символов."); return
    await state.update_data(pay_account=acc)
    await state.set_state(BuyStates.base_pct)
    await message.answer("3) Вознаграждение трейдера, % (−5.00..+25.00, шаг 0.01):")

@router.message(BuyStates.base_pct)
async def step_base(message: Message, state: FSMContext):
    try:
        v = float((message.text or "").replace(",", "."))
        if not validate_decimal_range_step(v, -5.00, 25.00, 0.01): raise ValueError
        await state.update_data(base_pct=round(v,2))
        s = await get_settings()
        await state.set_state(BuyStates.service_fee)
        await message.answer(f"4) Комиссия сервиса, % ({float(s.service_fee_min_pct):.2f}..{float(s.service_fee_max_pct):.2f}, шаг 0.01). По умолчанию {float(s.service_fee_default_pct):.2f}%")
    except Exception:
        await message.answer("Неверно. Введите число в диапазоне −5.00..+25.00 с шагом 0.01.")

@router.message(BuyStates.service_fee)
async def step_fee(message: Message, state: FSMContext):
    s = await get_settings()
    try:
        v = float((message.text or "").replace(",", "."))
        if not validate_decimal_range_step(v, float(s.service_fee_min_pct), float(s.service_fee_max_pct), 0.01): raise ValueError
        await state.update_data(service_fee_pct=round(v,2))
        await state.set_state(BuyStates.audience)
        await message.answer("5) Выберите аудиторию:", reply_markup=kb_audience())
    except Exception:
        await message.answer(f"Неверно. {float(s.service_fee_min_pct):.2f}..{float(s.service_fee_max_pct):.2f}, шаг 0.01.")

@router.callback_query(BuyStates.audience, F.data.startswith("aud:"))
async def step_audience(cq: CallbackQuery, state: FSMContext):
    mode = cq.data.split(":",1)[1]
    await state.update_data(audience_type=mode)
    await state.set_state(BuyStates.audience_params)
    if mode == "rep":
        await cq.message.answer("Укажите REP диапазон 'от до' (можно один конец). Пример: '10 100' или '10' или ' 100'")
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
            if len(parts) == 2: filt = {"rep_min": int(parts[0]), "rep_max": int(parts[1])}
            elif len(parts) == 1:
                try: filt = {"rep_min": int(parts[0])}
                except: filt = {"rep_max": int(parts[0])}
        elif t == "personal":
            filt = {"telegram_id": int(data)}
    except Exception:
        await message.answer("Неверный формат."); return
    await state.update_data(audience_filter=filt)
    await state.set_state(BuyStates.pay_time)
    await message.answer("6) Время на оплату (мин): 5/10/15/20/25/30")

@router.message(BuyStates.pay_time)
async def step_pay_time(message: Message, state: FSMContext):
    try:
        mins = int((message.text or "").strip())
        if mins not in (5,10,15,20,25,30): raise ValueError
        await state.update_data(pay_mins=mins)
        await state.set_state(BuyStates.email_toggle)
        await message.answer("7) Нужен e-mail для чека от банка? (Да/Нет)", reply_markup=kb_yesno("em:yes","em:no"))
    except Exception:
        await message.answer("Введите одно из значений: 5/10/15/20/25/30.")

@router.callback_query(BuyStates.email_toggle, F.data.startswith("em:"))
async def step_email_toggle(cq: CallbackQuery, state: FSMContext):
    yn = cq.data.split(":",1)[1]
    if yn == "yes":
        await state.set_state(BuyStates.email_value)
        await cq.message.answer("Введите e-mail (до 254 символов):")
    else:
        await state.update_data(email=None)
        await state.set_state(BuyStates.comment_toggle)
        await cq.message.answer("8) Комментарий к сделке? (Да/Нет)", reply_markup=kb_yesno("cmt:yes","cmt:no"))
    await cq.answer()

@router.message(BuyStates.email_value)
async def step_email_val(message: Message, state: FSMContext):
    email = (message.text or "").strip()
    if len(email) < 3 or len(email) > 254 or "@" not in email:
        await message.answer("Неверный e-mail."); return
    await state.update_data(email=email)
    await state.set_state(BuyStates.comment_toggle)
    await message.answer("8) Комментарий к сделке? (Да/Нет)", reply_markup=kb_yesno("cmt:yes","cmt:no"))

@router.callback_query(BuyStates.comment_toggle, F.data.startswith("cmt:"))
async def step_cmt_toggle(cq: CallbackQuery, state: FSMContext):
    yn = cq.data.split(":",1)[1]
    if yn == "yes":
        await state.set_state(BuyStates.comment_text)
        await cq.message.answer("Введите комментарий (до 1000 символов).")
    else:
        await state.update_data(comment=None)
        await state.set_state(BuyStates.warning_toggle)
        await cq.message.answer("9) Включить предупреждение? (Да/Нет)", reply_markup=kb_yesno("warn:yes","warn:no"))
    await cq.answer()

@router.message(BuyStates.comment_text)
async def step_cmt_text(message: Message, state: FSMContext):
    txt = (message.text or "").strip()
    if len(txt) > 1000:
        await message.answer("До 1000 символов."); return
    await state.update_data(comment=txt)
    await state.set_state(BuyStates.warning_toggle)
    await message.answer("9) Включить предупреждение? (Да/Нет)", reply_markup=kb_yesno("warn:yes","warn:no"))

@router.callback_query(BuyStates.warning_toggle, F.data.startswith("warn:"))
async def step_warn(cq: CallbackQuery, state: FSMContext):
    yn = cq.data.split(":",1)[1]
    await state.update_data(warning_enabled=(yn=="yes"))
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
    act = cq.data.split(":",1)[1]
    if act == "cancel":
        await state.clear(); await cq.message.answer("Отменено."); await cq.answer(); return
    data = await state.get_data()
    s = await get_settings()
    deal_no = datetime.now(TZ).strftime("%Y%m%d-") + __import__("secrets").token_hex(4).upper()[:7]
    async with async_session() as ss:
        u = await ensure_user(ss, cq.from_user)
        if u.role != RoleEnum.admin:
            await cq.answer("Только админ.", show_alert=True); return
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
        # сохраняем реквизиты
        deal.winner_card_bank = None
        deal.winner_card_mask = data['pay_account']
        deal.winner_card_holder = data.get('email')
        deal.pay_mins = int(data['pay_mins'])
        ss.add(deal); await ss.commit()
    await state.clear()
    await cq.message.answer(
        f"Сделка опубликована: ПОКУПКА\n№ <code>{deal_no}</code>\n"
        f"{data['amount_rub']:.2f} RUB (~{data['amount_usdt']:.2f} USDT)\n"
        f"База: {data['base_pct']:.2f}% • Комиссия сервиса: {data['service_fee_pct']:.2f}%\n"
        f"Оплата в течение: {data['pay_mins']} мин"
    )
    from app.handlers.buy_auction import broadcast_stage1
    await broadcast_stage1(int(deal.id))
    await schedule_stage1_end(int(deal.id), after_seconds=int(s.round_secs_default))
    await cq.answer()

3) Аукцион BUY (рассылка, «Принять/Отклонить», ставки)

Файл (НОВЫЙ): app/handlers/buy_auction.py

from __future__ import annotations
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from sqlalchemy import select
from zoneinfo import ZoneInfo
from datetime import datetime
import json

from app.db.base import async_session
from app.db.models import Deal, DealType, DealStatus, DealParticipant, User, Bid, DealRound
from app.services.settings import get_settings
from app.handlers.utils import ensure_user
from app.utils.money import bank_round_2

router = Router(name="buy_auction")
TZ = ZoneInfo("Europe/Amsterdam")

class BidStates(StatesGroup):
    waiting_bid = State()

def stage1_kb(deal_id: int):
    b = InlineKeyboardBuilder()
    b.button(text="Принять сделку (+1 REP)", callback_data=f"buy1:accept:{deal_id}")
    b.button(text="Отклонить сделку (-2 REP)", callback_data=f"buy1:decline:{deal_id}")
    b.button(text="⏳ ТАЙМЕР", callback_data="noop")
    b.adjust(1,1,1); return b.as_markup()

async def eligible_traders(deal: Deal) -> list[int]:
    async with async_session() as s:
        rows = (await s.execute(select(User.tg_id, User.id, User.is_online)
                                .where(User.role=="trader", User.is_active==True, User.is_online==True))).all()
        filt = json.loads(deal.audience_filter or "{}")
        out = []
        for tg_id, uid, online in rows:
            if not online: continue
            if deal.audience_type == "rep":
                rep = int((await s.scalar(select(User.rep).where(User.id==uid))) or 0)
                if "rep_min" in filt and rep < int(filt["rep_min"]): continue
                if "rep_max" in filt and rep > int(filt["rep_max"]): continue
            if deal.audience_type == "personal":
                if int(filt.get("telegram_id", 0)) != int(tg_id): continue
            out.append(int(tg_id))
        return out

async def broadcast_stage1(deal_id: int):
    from app.bot import create_bot_and_dp
    bot, _ = create_bot_and_dp("")
    async with async_session() as s:
        d = await s.get(Deal, deal_id)
        sset = await get_settings()
        max_reward = 0.0
        if float(d.base_pct) > 0:
            max_reward = bank_round_2(float(d.amount_rub) * float(d.base_pct)/100.0)
        txt = (
            "<b>НОВАЯ СДЕЛКА — АУКЦИОН (ПОКУПКА)</b>\n"
            f"Номер: <code>{d.deal_no}</code>\n"
            f"Вознаграждение (база): {float(d.base_pct):.2f}%\n"
            f"Сумма: {float(d.amount_rub):.2f} RUB (~{float(d.amount_usdt_snapshot):.2f} USDT)\n"
            f"Макс. вознаграждение (RUB): {max_reward:.2f}\n"
            f"Комиссия сервиса: {float(d.service_fee_pct):.2f}%\n"
            f"Время на оплату победителем: {int(d.pay_mins or 20)} мин\n"
            + (f"\n<b>Комментарий:</b>\n{d.comment}\n" if d.comment else "")
            + ("\n<b>!ПРЕДУПРЕЖДЕНИЕ!</b>\n!Одной суммой! Перевод строго по реквизитам.\n" if d.warning_enabled else "")
        )
        for tg_id in await eligible_traders(d):
            try:
                await bot.send_message(tg_id, txt, reply_markup=stage1_kb(d.id))
            except: pass

@router.callback_query(F.data.startswith("buy1:"))
async def stage1_click(cq: CallbackQuery):
    _, action, deal_id_s = cq.data.split(":",2)
    deal_id = int(deal_id_s)
    async with async_session() as s:
        u = await ensure_user(s, cq.from_user)
        d = await s.get(Deal, deal_id)
        if not d or d.status != DealStatus.OPEN:
            await cq.answer("Сделка недоступна.", show_alert=True); return
        row = await s.scalar(select(DealParticipant).where(DealParticipant.deal_id==deal_id, DealParticipant.user_id==u.id))
        if not row:
            s.add(DealParticipant(deal_id=deal_id, user_id=u.id, accepted=(action=="accept")))
        else:
            row.accepted = (action=="accept")
        u.rep += 1 if action=="accept" else -2
        await s.commit()
    await cq.message.answer("Отлично! +1 REP. Ждите второй этап… ⏳" if action=="accept" else "Очень жаль: −2 REP. Ожидайте другие сделки.")
    await cq.answer()

def bid_range_hint(base_pct: float) -> str:
    return f"Введите вашу ставку в диапазоне [-5.00; {base_pct:.2f}] с шагом 0.01"

async def notify_round_start(deal_id: int, round_number: int):
    from app.bot import create_bot_and_dp
    bot, _ = create_bot_and_dp("")
    async with async_session() as s:
        d = await s.get(Deal, deal_id)
        rows = await s.execute(select(DealParticipant, User.tg_id).join(User, DealParticipant.user_id==User.id)
                               .where(DealParticipant.deal_id==deal_id, DealParticipant.accepted==True))
        for p, tg_id in rows.all():
            try:
                await bot.send_message(int(tg_id),
                    f"<b>АУКЦИОН НАЧАЛСЯ — РАУНД {round_number}</b>\n"
                    f"{bid_range_hint(float(d.base_pct))}\n"
                    f"Отправьте число (например 0.25 или -1.50).")
            except: pass


(Приём ставок — общий, через уже существующий sell_auction.BidStates.waiting_bid у вас может быть. Если его нет — скопируйте аналогично из SELL. Главное: воркер завершает раунды.)

4) Победитель BUY: таймер оплаты, подтверждение админом, финализация

Файл (НОВЫЙ): app/handlers/buy_winner.py

from __future__ import annotations
from aiogram import Router, F
from aiogram.types import CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import select
from zoneinfo import ZoneInfo
from datetime import datetime, timedelta

from app.db.base import async_session
from app.db.models import Deal, DealStatus, User
from app.utils.money import bank_round_2
from app.scheduler import schedule_buy_timeout

router = Router(name="buy_winner")
TZ = ZoneInfo("Europe/Amsterdam")

def winner_kb(deal_id: int):
    b = InlineKeyboardBuilder()
    b.button(text="Я ВЫПОЛНИЛ ПЕРЕВОД", callback_data=f"buywin:paid:{deal_id}")
    b.button(text="ПЛОХИЕ РЕКВИЗИТЫ", callback_data=f"buywin:bad:{deal_id}")
    b.button(text="ОТМЕНА СДЕЛКИ −200 RUB", callback_data=f"buywin:cancel:{deal_id}")
    b.button(text="ОПЕРАТОР", callback_data=f"buywin:op:{deal_id}")
    b.adjust(1,2,1)
    return b.as_markup()

@router.callback_query(F.data.startswith("buyadm:confirm:"))
async def admin_confirm(cq: CallbackQuery):
    deal_id = int(cq.data.split(":",2)[2])
    async with async_session() as s:
        d = await s.get(Deal, deal_id)
        if not d: return await cq.answer()
        # Применяем экономику: +amount_rub к балансу, вознаграждение +/- и комиссия с положительного
        u = await s.get(User, int(d.winner_user_id))
        amount = float(d.amount_rub)
        reward_rub = bank_round_2(amount * float(d.winner_bid_pct)/100.0)
        fee_rub = bank_round_2(reward_rub * float(d.service_fee_pct)/100.0) if reward_rub > 0 else 0.0
        u.balance_rub = float(u.balance_rub) + amount + (reward_rub - fee_rub if reward_rub >= 0 else -abs(reward_rub))
        d.status = DealStatus.COMPLETED
        await s.commit()
    await cq.message.answer("Сделка завершена ✅")
    await cq.answer()

@router.callback_query(F.data.startswith("buyadm:reject:"))
async def admin_reject(cq: CallbackQuery):
    await cq.message.answer("Отклонено. Свяжитесь с трейдером/оператором.")
    await cq.answer()

@router.callback_query(F.data.startswith("buywin:paid:"))
async def winner_paid(cq: CallbackQuery):
    deal_id = int(cq.data.split(":",2)[2])
    # Оповещаем админов для подтверждения
    from app.bot import create_bot_and_dp
    from app.config import settings
    bot, _ = create_bot_and_dp("")
    async with async_session() as s:
        d = await s.get(Deal, deal_id)
        if not d: return await cq.answer()
        text_admin = (
            "<b>ПОКУПКА — отметка оплаты от победителя</b>\n"
            f"Сделка № <code>{d.deal_no}</code>\n"
            f"Сумма: {float(d.amount_rub):.2f} RUB (~{float(d.amount_usdt_snapshot):.2f} USDT)\n"
            f"Реквизиты для оплаты: {d.winner_card_mask}\n"
            f"E-mail: {d.winner_card_holder or '-'}\n\n"
            "Подтвердить получение перевода?"
        )
        kb = InlineKeyboardBuilder()
        kb.button(text="Подтвердить", callback_data=f"buyadm:confirm:{deal_id}")
        kb.button(text="Отклонить", callback_data=f"buyadm:reject:{deal_id}")
        kb.adjust(2)
        for admin in settings.admins:
            try: await bot.send_message(admin, text_admin, reply_markup=kb.as_markup())
            except: pass
    await cq.message.answer("Сообщение администратору отправлено. Ожидайте подтверждения.")
    await cq.answer()

@router.callback_query(F.data.startswith("buywin:cancel:"))
async def winner_cancel(cq: CallbackQuery):
    deal_id = int(cq.data.split(":",2)[2])
    async with async_session() as s:
        d = await s.get(Deal, deal_id)
        if not d: return await cq.answer()
        u = await s.get(User, int(d.winner_user_id))
        u.balance_rub = float(u.balance_rub) - 200.0
        d.status = DealStatus.CANCELLED_BY_TRADER
        await s.commit()
    await cq.message.answer("Сделка отменена. Штраф −200 RUB применён.")
    await cq.answer()

@router.callback_query(F.data.startswith("buywin:bad:"))
async def winner_bad_reqs(cq: CallbackQuery):
    await cq.message.answer("Отмечено. Реквизиты будут проверены администратором.")
    await cq.answer()

5) Воркер — после выбора победителя для BUY

Файл: app/workers/auction.py

Функция выбора уведомителя — добавь явный путь для BUY:

from app.db.models import DealType

def _resolve_notify_round_start(deal_type: DealType):
    if deal_type == DealType.SELL:
        from app.handlers.sell_auction import notify_round_start as _notify
        return _notify
    if deal_type == DealType.BUY:
        from app.handlers.buy_auction import notify_round_start as _notify
        return _notify
    return None


После назначения победителя — разветвление по типу:
замени вызов финализации после deal.status = WINNER_ASSIGNED на:

if deal.deal_type == DealType.SELL:
    await _after_winner_assigned(None, deal_id=deal_id)   # как раньше для SELL
else:
    await _after_winner_assigned_buy(deal_id)             # НОВОЕ для BUY


Новая функция для BUY:

async def _after_winner_assigned_buy(deal_id: int):
    from app.bot import create_bot_and_dp
    from app.scheduler import schedule_buy_timeout
    async with async_session() as s:
        d = await s.get(Deal, deal_id)
        if not d: return
        d.status = DealStatus.WINNER_ASSIGNED  # фиксируем
        # старт окна оплаты победителем
        mins = int(getattr(d, "pay_mins", 20) or 20)
        from datetime import datetime, timedelta
        d.pay_deadline_at = datetime.now(TZ) + timedelta(minutes=mins)
        await s.commit()
    # уведомление победителю
    bot, _ = create_bot_and_dp("")
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
    await schedule_buy_timeout(d.id, after_minutes=mins)


Таймаут BUY — добавь в воркере:

async def buy_pay_timeout(deal_id: int):
    # истёк таймер оплаты: списываем сумму сделки с баланса трейдера (штраф)
    async with async_session() as s:
        d = await s.get(Deal, deal_id)
        if not d or d.deal_type != DealType.BUY: return
        if d.winner_user_id:
            u = await s.get(User, int(d.winner_user_id))
            u.balance_rub = float(u.balance_rub) - float(d.amount_rub)
        d.status = DealStatus.CANCELLED_TIMEOUT
        await s.commit()


Файл: app/scheduler.py — добавь планировщик BUY-таймаута:

async def schedule_buy_timeout(deal_id: int, after_minutes: int):
    get_scheduler().add_job(
        "app.workers.auction:buy_pay_timeout", "date",
        run_date=datetime.now(TZ)+timedelta(minutes=after_minutes),
        args=[deal_id], id=f"deal:{deal_id}:buy_pay", replace_existing=True
    )

6) Подключение новых роутеров

Файл: app/bot.py

from app.handlers.buy_master import router as buy_master_router
from app.handlers.buy_auction import router as buy_auction_router
from app.handlers.buy_winner import router as buy_winner_router

# внутри create_bot_and_dp(...)
dp.include_router(buy_master_router)
dp.include_router(buy_auction_router)
dp.include_router(buy_winner_router)


Никаких изменений в SELL-роутах — оставьте как есть.

---

**Cursor**

Я начну с поиска и чтения нужных файлов, чтобы внести правки и добавить новые модули для BUY, не затрагивая SELL. Затем создам новые файлы и обновлю роутеры и воркер.

Я сначала посмотрю текущие файлы `app/handlers/admin.py`, `app/bot.py`, `app/workers/auction.py` и `app/scheduler.py`, чтобы корректно внести правки и добавить новые модули для BUY. Затем сразу начну правки админ-меню.

Я обновлю админ-меню, затем создам новые модули BUY и подключу их, после чего изменю воркер и планировщик.

```python
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

def _resolve_buy_entry():
    """
    Пытаемся найти способ запустить мастер ПОКУПКИ.
    Возвращает кортеж (mode, payload):
      mode == "func"  -> payload = callable(message, state)
      mode == "states"-> payload = StatesGroupClass (у которого есть amount_rub)
      mode == None    -> не нашли
    """
    candidates = (
        "app.handlers.admin_deals",
        "app.handlers.admin_deals_buy",
        "app.handlers.buy_master",
        "app.handlers.auction_buy",
    )
    for modname in candidates:
        mod = _try_import(modname)
        if not mod:
            continue
        for fname in ("buy_menu", "open_buy_master", "start_buy_master"):
            fn = getattr(mod, fname, None)
            if callable(fn):
                return ("func", fn)
        for obj in vars(mod).values():
            try:
                if isinstance(obj, type) and issubclass(obj, StatesGroup) and hasattr(obj, "amount_rub"):
                    return ("states", obj)
            except Exception:
                pass
    return (None, None)

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
    b.button(text="Торги → Покупка (мастер)", callback_data="admin:buy_new")
    b.button(text="Торги → Продажа USDT (мастер)", callback_data="admin:sell")
    b.button(text="Настройки → Финансы", callback_data="admin:settings_fin")
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

@router.callback_query(F.data == "admin:sell")
async def admin_open_sell(cq: CallbackQuery, state: FSMContext):
    async with async_session() as s:
        u = await ensure_user(s, cq.from_user)
        if u.role != RoleEnum.admin:
            await cq.answer("Доступно только администратору.", show_alert=True)
            return
    # Стартуем мастер ПРОДАЖИ напрямую (не вызываем message-хендлер)
    from app.handlers.admin_deals_sell import SellStates
    await state.clear()
    await state.set_state(SellStates.amount_rub)
    await cq.message.answer("1) Введите сумму сделки в RUB:")
    await cq.answer()

@router.callback_query(F.data == "admin:buy_new")
async def admin_open_buy_new(cq: CallbackQuery, state: FSMContext):
    async with async_session() as s:
        u = await ensure_user(s, cq.from_user)
        if u.role != RoleEnum.admin:
            await cq.answer("Доступно только администратору.", show_alert=True)
            return
    from app.handlers.buy_master import BuyStates
    await state.clear()
    await state.set_state(BuyStates.amount_rub)
    await cq.message.answer("1) Введите сумму сделки в RUB:")
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

```

```python
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
    deal_no = datetime.now(TZ).strftime("%Y%m%d-") + __import__("secrets").token_hex(4).upper()[:7]
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
    await broadcast_stage1(int(deal.id))
    await schedule_stage1_end(int(deal.id), after_seconds=int(s.round_secs_default))
    await cq.answer()


```

```python
from __future__ import annotations
from aiogram import Router, F
from aiogram.types import CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from sqlalchemy import select
from zoneinfo import ZoneInfo
from datetime import datetime
import json

from app.db.base import async_session
from app.db.models import Deal, DealType, DealStatus, DealParticipant, User, Bid, DealRound
from app.services.settings import get_settings
from app.handlers.utils import ensure_user
from app.utils.money import bank_round_2

router = Router(name="buy_auction")
TZ = ZoneInfo("Europe/Amsterdam")


class BidStates(StatesGroup):
    waiting_bid = State()


def stage1_kb(deal_id: int):
    b = InlineKeyboardBuilder()
    b.button(text="Принять сделку (+1 REP)", callback_data=f"buy1:accept:{deal_id}")
    b.button(text="Отклонить сделку (-2 REP)", callback_data=f"buy1:decline:{deal_id}")
    b.button(text="⏳ ТАЙМЕР", callback_data="noop")
    b.adjust(1, 1, 1)
    return b.as_markup()


async def eligible_traders(deal: Deal) -> list[int]:
    async with async_session() as s:
        rows = (
            await s.execute(
                select(User.tg_id, User.id, User.is_online).where(
                    User.role == "trader", User.is_active == True, User.is_online == True
                )
            )
        ).all()
        filt = json.loads(deal.audience_filter or "{}")
        out: list[int] = []
        for tg_id, uid, online in rows:
            if not online:
                continue
            if deal.audience_type == "rep":
                rep = int((await s.scalar(select(User.rep).where(User.id == uid))) or 0)
                if "rep_min" in filt and rep < int(filt["rep_min"]):
                    continue
                if "rep_max" in filt and rep > int(filt["rep_max"]):
                    continue
            if deal.audience_type == "personal":
                if int(filt.get("telegram_id", 0)) != int(tg_id):
                    continue
            out.append(int(tg_id))
        return out


async def broadcast_stage1(deal_id: int):
    from app.bot import create_bot_and_dp
    bot, _ = create_bot_and_dp("")
    async with async_session() as s:
        d = await s.get(Deal, deal_id)
        sset = await get_settings()
        max_reward = 0.0
        if float(d.base_pct) > 0:
            max_reward = bank_round_2(float(d.amount_rub) * float(d.base_pct) / 100.0)
        txt = (
            "<b>НОВАЯ СДЕЛКА — АУКЦИОН (ПОКУПКА)</b>\n"
            f"Номер: <code>{d.deal_no}</code>\n"
            f"Вознаграждение (база): {float(d.base_pct):.2f}%\n"
            f"Сумма: {float(d.amount_rub):.2f} RUB (~{float(d.amount_usdt_snapshot):.2f} USDT)\n"
            f"Макс. вознаграждение (RUB): {max_reward:.2f}\n"
            f"Комиссия сервиса: {float(d.service_fee_pct):.2f}%\n"
            f"Время на оплату победителем: {int(d.pay_mins or 20)} мин\n"
            + (f"\n<b>Комментарий:</b>\n{d.comment}\n" if d.comment else "")
            + (
                "\n<b>!ПРЕДУПРЕЖДЕНИЕ!</b>\n!Одной суммой! Перевод строго по реквизитам.\n"
                if d.warning_enabled
                else ""
            )
        )
        for tg_id in await eligible_traders(d):
            try:
                await bot.send_message(tg_id, txt, reply_markup=stage1_kb(d.id))
            except:  # noqa: E722
                pass


@router.callback_query(F.data.startswith("buy1:"))
async def stage1_click(cq: CallbackQuery):
    _, action, deal_id_s = cq.data.split(":", 2)
    deal_id = int(deal_id_s)
    async with async_session() as s:
        u = await ensure_user(s, cq.from_user)
        d = await s.get(Deal, deal_id)
        if not d or d.status != DealStatus.OPEN:
            await cq.answer("Сделка недоступна.", show_alert=True)
            return
        row = await s.scalar(
            select(DealParticipant).where(
                DealParticipant.deal_id == deal_id, DealParticipant.user_id == u.id
            )
        )
        if not row:
            s.add(DealParticipant(deal_id=deal_id, user_id=u.id, accepted=(action == "accept")))
        else:
            row.accepted = (action == "accept")
        u.rep += 1 if action == "accept" else -2
        await s.commit()
    await cq.message.answer(
        "Отлично! +1 REP. Ждите второй этап… ⏳"
        if action == "accept"
        else "Очень жаль: −2 REP. Ожидайте другие сделки."
    )
    await cq.answer()


def bid_range_hint(base_pct: float) -> str:
    return f"Введите вашу ставку в диапазоне [-5.00; {base_pct:.2f}] с шагом 0.01"


async def notify_round_start(deal_id: int, round_number: int):
    from app.bot import create_bot_and_dp
    bot, _ = create_bot_and_dp("")
    async with async_session() as s:
        d = await s.get(Deal, deal_id)
        rows = await s.execute(
            select(DealParticipant, User.tg_id).join(User, DealParticipant.user_id == User.id).where(
                DealParticipant.deal_id == deal_id, DealParticipant.accepted == True
            )
        )
        for p, tg_id in rows.all():
            try:
                await bot.send_message(
                    int(tg_id),
                    f"<b>АУКЦИОН НАЧАЛСЯ — РАУНД {round_number}</b>\n"
                    f"{bid_range_hint(float(d.base_pct))}\n"
                    f"Отправьте число (например 0.25 или -1.50).",
                )
            except:  # noqa: E722
                pass


```

```python
from __future__ import annotations
from aiogram import Router, F
from aiogram.types import CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
from zoneinfo import ZoneInfo
from datetime import datetime, timedelta
from sqlalchemy import select

from app.db.base import async_session
from app.db.models import Deal, DealStatus, User
from app.utils.money import bank_round_2
from app.scheduler import schedule_buy_timeout

router = Router(name="buy_winner")
TZ = ZoneInfo("Europe/Amsterdam")


def winner_kb(deal_id: int):
    b = InlineKeyboardBuilder()
    b.button(text="Я ВЫПОЛНИЛ ПЕРЕВОД", callback_data=f"buywin:paid:{deal_id}")
    b.button(text="ПЛОХИЕ РЕКВИЗИТЫ", callback_data=f"buywin:bad:{deal_id}")
    b.button(text="ОТМЕНА СДЕЛКИ −200 RUB", callback_data=f"buywin:cancel:{deal_id}")
    b.button(text="ОПЕРАТОР", callback_data=f"buywin:op:{deal_id}")
    b.adjust(1, 2, 1)
    return b.as_markup()


@router.callback_query(F.data.startswith("buyadm:confirm:"))
async def admin_confirm(cq: CallbackQuery):
    deal_id = int(cq.data.split(":", 2)[2])
    async with async_session() as s:
        d = await s.get(Deal, deal_id)
        if not d:
            return await cq.answer()
        u = await s.get(User, int(d.winner_user_id))
        amount = float(d.amount_rub)
        reward_rub = bank_round_2(amount * float(d.winner_bid_pct) / 100.0)
        fee_rub = bank_round_2(reward_rub * float(d.service_fee_pct) / 100.0) if reward_rub > 0 else 0.0
        u.balance_rub = float(u.balance_rub) + amount + (reward_rub - fee_rub if reward_rub >= 0 else -abs(reward_rub))
        d.status = DealStatus.COMPLETED
        await s.commit()
    await cq.message.answer("Сделка завершена ✅")
    await cq.answer()


@router.callback_query(F.data.startswith("buyadm:reject:"))
async def admin_reject(cq: CallbackQuery):
    await cq.message.answer("Отклонено. Свяжитесь с трейдером/оператором.")
    await cq.answer()


@router.callback_query(F.data.startswith("buywin:paid:"))
async def winner_paid(cq: CallbackQuery):
    deal_id = int(cq.data.split(":", 2)[2])
    from app.bot import create_bot_and_dp
    from app.config import settings
    bot, _ = create_bot_and_dp("")
    async with async_session() as s:
        d = await s.get(Deal, deal_id)
        if not d:
            return await cq.answer()
        text_admin = (
            "<b>ПОКУПКА — отметка оплаты от победителя</b>\n"
            f"Сделка № <code>{d.deal_no}</code>\n"
            f"Сумма: {float(d.amount_rub):.2f} RUB (~{float(d.amount_usdt_snapshot):.2f} USDT)\n"
            f"Реквизиты для оплаты: {d.winner_card_mask}\n"
            f"E-mail: {d.winner_card_holder or '-'}\n\n"
            "Подтвердить получение перевода?"
        )
        kb = InlineKeyboardBuilder()
        kb.button(text="Подтвердить", callback_data=f"buyadm:confirm:{deal_id}")
        kb.button(text="Отклонить", callback_data=f"buyadm:reject:{deal_id}")
        kb.adjust(2)
        for admin in settings.admins:
            try:
                await bot.send_message(admin, text_admin, reply_markup=kb.as_markup())
            except:  # noqa: E722
                pass
    await cq.message.answer("Сообщение администратору отправлено. Ожидайте подтверждения.")
    await cq.answer()


@router.callback_query(F.data.startswith("buywin:cancel:"))
async def winner_cancel(cq: CallbackQuery):
    deal_id = int(cq.data.split(":", 2)[2])
    async with async_session() as s:
        d = await s.get(Deal, deal_id)
        if not d:
            return await cq.answer()
        u = await s.get(User, int(d.winner_user_id))
        u.balance_rub = float(u.balance_rub) - 200.0
        d.status = DealStatus.CANCELLED_BY_TRADER
        await s.commit()
    await cq.message.answer("Сделка отменена. Штраф −200 RUB применён.")
    await cq.answer()


@router.callback_query(F.data.startswith("buywin:bad:"))
async def winner_bad_reqs(cq: CallbackQuery):
    await cq.message.answer("Отмечено. Реквизиты будут проверены администратором.")
    await cq.answer()


```

```python
*** Begin Patch
*** Update File: app/workers/auction.py
@@
 def _resolve_notify_round_start(deal_type: DealType):
     """Возвращает функцию notify_round_start(deal_id: int, round_number: int) для нужного типа сделки."""
     if deal_type == DealType.SELL:
         from app.handlers.sell_auction import notify_round_start as _notify
         return _notify
+    if deal_type == DealType.BUY:
+        from app.handlers.buy_auction import notify_round_start as _notify
+        return _notify
@@
-        deal.winner_user_id = users[0]
-        deal.winner_bid_pct = min_pct
-        deal.status = DealStatus.WINNER_ASSIGNED
-        await s.commit()
-    await _after_winner_assigned(None, deal_id=deal_id)
+        deal.winner_user_id = users[0]
+        deal.winner_bid_pct = min_pct
+        deal.status = DealStatus.WINNER_ASSIGNED
+        await s.commit()
+    if deal.deal_type == DealType.SELL:
+        await _after_winner_assigned(None, deal_id=deal_id)
+    else:
+        await _after_winner_assigned_buy(deal_id)
@@
 async def _after_winner_assigned(s_in: any = None, deal_id: int | None = None, deal: Deal | None = None):
@@
     await schedule_pay_timeout(deal.id, after_minutes=pay_minutes)
+
+async def _after_winner_assigned_buy(deal_id: int):
+    from app.bot import create_bot_and_dp
+    from app.scheduler import schedule_buy_timeout
+    from app.db.models import User
+    async with async_session() as s:
+        d = await s.get(Deal, deal_id)
+        if not d:
+            return
+        d.status = DealStatus.WINNER_ASSIGNED
+        mins = int(getattr(d, "pay_mins", 20) or 20)
+        d.pay_deadline_at = datetime.now(TZ) + timedelta(minutes=mins)
+        await s.commit()
+    bot, _ = create_bot_and_dp("")
+    async with async_session() as s:
+        d = await s.get(Deal, deal_id)
+        await bot.send_message(
+            int(await _winner_tg_id(d.winner_user_id)),
+            (
+                "<b>ВЫ ВЫИГРАЛИ АУКЦИОН</b>\nВид: ПОКУПКА\n"
+                f"Сделка № <code>{d.deal_no}</code>\n"
+                f"Ваше вознаграждение: {float(d.winner_bid_pct):.2f}%\n"
+                f"Сумма: {float(d.amount_rub):.2f} RUB (~{float(d.amount_usdt_snapshot):.2f} USDT)\n"
+                f"Переведите на реквизиты: {d.winner_card_mask}\nE-mail для чека: {d.winner_card_holder or '-'}\n"
+                f"Дедлайн: до {d.pay_deadline_at.astimezone(TZ).strftime('%H:%M')}"
+            ),
+            reply_markup=__import__("app.handlers.buy_winner", fromlist=["winner_kb"]).winner_kb(d.id)
+        )
+    await schedule_buy_timeout(d.id, after_minutes=mins)
@@
 async def trader_confirm_timeout(deal_id: int):
@@
     await s.commit()
+
+async def buy_pay_timeout(deal_id: int):
+    async with async_session() as s:
+        d = await s.get(Deal, deal_id)
+        if not d or d.deal_type != DealType.BUY:
+            return
+        if d.winner_user_id:
+            u = await s.get(User, int(d.winner_user_id))
+            u.balance_rub = float(u.balance_rub) - float(d.amount_rub)
+        d.status = DealStatus.CANCELLED_TIMEOUT
+        await s.commit()
*** End Patch
```

Продолжаю: перечитаю актуальную версию `app/workers/auction.py` и внесу правки для BUY уведомлений и финализации, затем добавлю таймаут.

I'll update the worker to add BUY-specific notify/flow and timeouts, then add the BUY timeout scheduler and include new routers in the bot.

```python
*** Begin Patch
*** Update File: app/workers/auction.py
@@
 def _resolve_notify_round_start(deal_type: DealType):
     """Возвращает функцию notify_round_start(deal_id: int, round_number: int) для нужного типа сделки."""
     if deal_type == DealType.SELL:
         from app.handlers.sell_auction import notify_round_start as _notify
         return _notify
+    if deal_type == DealType.BUY:
+        from app.handlers.buy_auction import notify_round_start as _notify
+        return _notify
     # Попытки найти BUY-модуль
     for modpath in ("app.handlers.buy_auction", "app.handlers.auction_buy", "app.handlers.auction"):
         try:
             mod = __import__(modpath, fromlist=["notify_round_start"])
             return getattr(mod, "notify_round_start")
@@
-        deal.winner_user_id = users[0]
-        deal.winner_bid_pct = min_pct
-        deal.status = DealStatus.WINNER_ASSIGNED
-        await s.commit()
-    await _after_winner_assigned(None, deal_id=deal_id)
+        deal.winner_user_id = users[0]
+        deal.winner_bid_pct = min_pct
+        deal.status = DealStatus.WINNER_ASSIGNED
+        await s.commit()
+    if deal.deal_type == DealType.SELL:
+        await _after_winner_assigned(None, deal_id=deal_id)
+    else:
+        await _after_winner_assigned_buy(deal_id)
@@
 async def _after_winner_assigned(s_in: any = None, deal_id: int | None = None, deal: Deal | None = None):
@@
     await schedule_pay_timeout(deal.id, after_minutes=pay_minutes)
+
+async def _after_winner_assigned_buy(deal_id: int):
+    from app.bot import create_bot_and_dp
+    from app.scheduler import schedule_buy_timeout
+    async with async_session() as s:
+        d = await s.get(Deal, deal_id)
+        if not d:
+            return
+        d.status = DealStatus.WINNER_ASSIGNED
+        mins = int(getattr(d, "pay_mins", 20) or 20)
+        d.pay_deadline_at = datetime.now(TZ) + timedelta(minutes=mins)
+        await s.commit()
+    bot, _ = create_bot_and_dp("")
+    async with async_session() as s:
+        d = await s.get(Deal, deal_id)
+        await bot.send_message(
+            int(await _winner_tg_id(d.winner_user_id)),
+            (
+                "<b>ВЫ ВЫИГРАЛИ АУКЦИОН</b>\nВид: ПОКУПКА\n"
+                f"Сделка № <code>{d.deal_no}</code>\n"
+                f"Ваше вознаграждение: {float(d.winner_bid_pct):.2f}%\n"
+                f"Сумма: {float(d.amount_rub):.2f} RUB (~{float(d.amount_usdt_snapshot):.2f} USDT)\n"
+                f"Переведите на реквизиты: {d.winner_card_mask}\nE-mail для чека: {d.winner_card_holder or '-'}\n"
+                f"Дедлайн: до {d.pay_deadline_at.astimezone(TZ).strftime('%H:%M')}"
+            ),
+            reply_markup=__import__("app.handlers.buy_winner", fromlist=["winner_kb"]).winner_kb(d.id)
+        )
+    await schedule_buy_timeout(d.id, after_minutes=mins)
@@
 async def trader_confirm_timeout(deal_id: int):
@@
     await s.commit()
+
+async def buy_pay_timeout(deal_id: int):
+    async with async_session() as s:
+        d = await s.get(Deal, deal_id)
+        if not d or d.deal_type != DealType.BUY:
+            return
+        if d.winner_user_id:
+            u = await s.get(User, int(d.winner_user_id))
+            u.balance_rub = float(u.balance_rub) - float(d.amount_rub)
+        d.status = DealStatus.CANCELLED_TIMEOUT
+        await s.commit()
*** End Patch
```

I'll update the worker logic precisely at the identified sections, then add the BUY timeout scheduler and include new routers in the bot.

```python
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
TZ = ZoneInfo("Europe/Amsterdam")

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

async def end_stage1(deal_id: int):
    async with async_session() as s:
        deal = await s.get(Deal, deal_id)
        if not deal or deal.status != DealStatus.OPEN:
            return
        deal.status = DealStatus.BIDDING
        round_secs = int(deal.round_secs or 20)
        dr = DealRound(
            deal_id=deal.id, round_number=1,
            started_at=datetime.now(TZ),
            deadline_at=datetime.now(TZ) + timedelta(seconds=round_secs)
        )
        s.add(dr); await s.commit()
    from app.scheduler import schedule_round_end
    await schedule_round_end(deal_id, 1, round_secs)
    _notify = _resolve_notify_round_start(deal.deal_type)
    if _notify:
        await _notify(deal_id, 1)

async def end_round(deal_id: int, round_number: int):
    async with async_session() as s:
        deal = await s.get(Deal, deal_id)
        if not deal or deal.status != DealStatus.BIDDING:
            return
        res = await s.execute(
            select(Bid.pct).where(Bid.deal_id==deal_id, Bid.round_number==round_number).order_by(asc(Bid.pct))
        )
        rows = [float(r[0]) for r in res.all()]
        if not rows:
            deal.status = DealStatus.CANCELLED
            await s.commit(); return
        min_pct = rows[0]
        res2 = await s.execute(
            select(Bid.user_id).where(Bid.deal_id==deal_id, Bid.round_number==round_number, Bid.pct==min_pct)
        )
        users = [int(r[0]) for r in res2.all()]
        if len(users) > 1:
            if round_number >= int(deal.max_tie_rounds or 5):
                import random
                deal.winner_user_id = random.choice(users)
                deal.winner_bid_pct = min_pct
                deal.status = DealStatus.WINNER_ASSIGNED
                await s.commit()
                await _after_winner_assigned(None, deal_id=deal_id)
                return
            next_round = round_number + 1
            dr = DealRound(
                deal_id=deal.id, round_number=next_round,
                started_at=datetime.now(TZ),
                deadline_at=datetime.now(TZ) + timedelta(seconds=int(deal.round_secs or 20))
            )
            s.add(dr); await s.commit()
            from app.scheduler import schedule_round_end
            await schedule_round_end(deal_id, next_round, int(deal.round_secs or 20))
            _notify = _resolve_notify_round_start(deal.deal_type)
            if _notify:
                await _notify(deal_id, next_round)
            return
        deal.winner_user_id = users[0]
        deal.winner_bid_pct = min_pct
        deal.status = DealStatus.WINNER_ASSIGNED
        await s.commit()
    if deal.deal_type == DealType.SELL:
        await _after_winner_assigned(None, deal_id=deal_id)
    else:
        await _after_winner_assigned_buy(deal_id)

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
    deal.pay_deadline_at = datetime.now(TZ) + timedelta(minutes=pay_minutes)
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
    await schedule_pay_timeout(deal.id, after_minutes=pay_minutes)

async def pay_timeout(deal_id: int):
    async with async_session() as s:
        d = await s.get(Deal, deal_id)
        if not d or d.status not in (DealStatus.WINNER_ASSIGNED, DealStatus.WAITING_ADMIN_TRANSFER):
            return
        if d.winner_user_id:
            await release_usdt(int(d.winner_user_id), float(d.amount_usdt_snapshot))
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

```

```python
*** Begin Patch
*** Update File: app/workers/auction.py
@@
 async def _after_winner_assigned(s_in: any = None, deal_id: int | None = None, deal: Deal | None = None):
@@
     await schedule_pay_timeout(deal.id, after_minutes=pay_minutes)
+
+async def _after_winner_assigned_buy(deal_id: int):
+    from app.bot import create_bot_and_dp
+    from app.scheduler import schedule_buy_timeout
+    async with async_session() as s:
+        d = await s.get(Deal, deal_id)
+        if not d:
+            return
+        d.status = DealStatus.WINNER_ASSIGNED
+        mins = int(getattr(d, "pay_mins", 20) or 20)
+        d.pay_deadline_at = datetime.now(TZ) + timedelta(minutes=mins)
+        await s.commit()
+    bot, _ = create_bot_and_dp("")
+    async with async_session() as s:
+        d = await s.get(Deal, deal_id)
+        await bot.send_message(
+            int(await _winner_tg_id(d.winner_user_id)),
+            (
+                "<b>ВЫ ВЫИГРАЛИ АУКЦИОН</b>\nВид: ПОКУПКА\n"
+                f"Сделка № <code>{d.deal_no}</code>\n"
+                f"Ваше вознаграждение: {float(d.winner_bid_pct):.2f}%\n"
+                f"Сумма: {float(d.amount_rub):.2f} RUB (~{float(d.amount_usdt_snapshot):.2f} USDT)\n"
+                f"Переведите на реквизиты: {d.winner_card_mask}\nE-mail для чека: {d.winner_card_holder or '-'}\n"
+                f"Дедлайн: до {d.pay_deadline_at.astimezone(TZ).strftime('%H:%M')}"
+            ),
+            reply_markup=__import__("app.handlers.buy_winner", fromlist=["winner_kb"]).winner_kb(d.id)
+        )
+    await schedule_buy_timeout(d.id, after_minutes=mins)
@@
 async def trader_confirm_timeout(deal_id: int):
@@
     await s.commit()
+
+async def buy_pay_timeout(deal_id: int):
+    async with async_session() as s:
+        d = await s.get(Deal, deal_id)
+        if not d or d.deal_type != DealType.BUY:
+            return
+        if d.winner_user_id:
+            u = await s.get(User, int(d.winner_user_id))
+            u.balance_rub = float(u.balance_rub) - float(d.amount_rub)
+        d.status = DealStatus.CANCELLED_TIMEOUT
+        await s.commit()
*** End Patch
```

I'll add the BUY-specific functions into the auction worker, extend the scheduler with schedule_buy_timeout, and include the new BUY routers in the bot. Then I'll lint the changed files.

```python
*** Begin Patch
*** Update File: app/workers/auction.py
@@
 async def _after_winner_assigned(s_in: any = None, deal_id: int | None = None, deal: Deal | None = None):
@@
     await schedule_pay_timeout(deal.id, after_minutes=pay_minutes)
+
+async def _after_winner_assigned_buy(deal_id: int):
+    from app.bot import create_bot_and_dp
+    from app.scheduler import schedule_buy_timeout
+    async with async_session() as s:
+        d = await s.get(Deal, deal_id)
+        if not d:
+            return
+        d.status = DealStatus.WINNER_ASSIGNED
+        mins = int(getattr(d, "pay_mins", 20) or 20)
+        d.pay_deadline_at = datetime.now(TZ) + timedelta(minutes=mins)
+        await s.commit()
+    bot, _ = create_bot_and_dp("")
+    async with async_session() as s:
+        d = await s.get(Deal, deal_id)
+        await bot.send_message(
+            int(await _winner_tg_id(d.winner_user_id)),
+            (
+                "<b>ВЫ ВЫИГРАЛИ АУКЦИОН</b>\nВид: ПОКУПКА\n"
+                f"Сделка № <code>{d.deal_no}</code>\n"
+                f"Ваше вознаграждение: {float(d.winner_bid_pct):.2f}%\n"
+                f"Сумма: {float(d.amount_rub):.2f} RUB (~{float(d.amount_usdt_snapshot):.2f} USDT)\n"
+                f"Переведите на реквизиты: {d.winner_card_mask}\nE-mail для чека: {d.winner_card_holder or '-'}\n"
+                f"Дедлайн: до {d.pay_deadline_at.astimezone(TZ).strftime('%H:%M')}"
+            ),
+            reply_markup=__import__("app.handlers.buy_winner", fromlist=["winner_kb"]).winner_kb(d.id)
+        )
+    await schedule_buy_timeout(d.id, after_minutes=mins)
@@
 async def trader_confirm_timeout(deal_id: int):
@@
     await s.commit()
+
+async def buy_pay_timeout(deal_id: int):
+    async with async_session() as s:
+        d = await s.get(Deal, deal_id)
+        if not d or d.deal_type != DealType.BUY:
+            return
+        if d.winner_user_id:
+            u = await s.get(User, int(d.winner_user_id))
+            u.balance_rub = float(u.balance_rub) - float(d.amount_rub)
+        d.status = DealStatus.CANCELLED_TIMEOUT
+        await s.commit()
*** End Patch
```

Добавлю новые функции в `app/workers/auction.py`, затем обновлю планировщик и маршрутизатор бота.

```python
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
TZ = ZoneInfo("Europe/Amsterdam")

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

async def end_stage1(deal_id: int):
    async with async_session() as s:
        deal = await s.get(Deal, deal_id)
        if not deal or deal.status != DealStatus.OPEN:
            return
        deal.status = DealStatus.BIDDING
        round_secs = int(deal.round_secs or 20)
        dr = DealRound(
            deal_id=deal.id, round_number=1,
            started_at=datetime.now(TZ),
            deadline_at=datetime.now(TZ) + timedelta(seconds=round_secs)
        )
        s.add(dr); await s.commit()
    from app.scheduler import schedule_round_end
    await schedule_round_end(deal_id, 1, round_secs)
    _notify = _resolve_notify_round_start(deal.deal_type)
    if _notify:
        await _notify(deal_id, 1)

async def end_round(deal_id: int, round_number: int):
    async with async_session() as s:
        deal = await s.get(Deal, deal_id)
        if not deal or deal.status != DealStatus.BIDDING:
            return
        res = await s.execute(
            select(Bid.pct).where(Bid.deal_id==deal_id, Bid.round_number==round_number).order_by(asc(Bid.pct))
        )
        rows = [float(r[0]) for r in res.all()]
        if not rows:
            deal.status = DealStatus.CANCELLED
            await s.commit(); return
        min_pct = rows[0]
        res2 = await s.execute(
            select(Bid.user_id).where(Bid.deal_id==deal_id, Bid.round_number==round_number, Bid.pct==min_pct)
        )
        users = [int(r[0]) for r in res2.all()]
        if len(users) > 1:
            if round_number >= int(deal.max_tie_rounds or 5):
                import random
                deal.winner_user_id = random.choice(users)
                deal.winner_bid_pct = min_pct
                deal.status = DealStatus.WINNER_ASSIGNED
                await s.commit()
                await _after_winner_assigned(None, deal_id=deal_id)
                return
            next_round = round_number + 1
            dr = DealRound(
                deal_id=deal.id, round_number=next_round,
                started_at=datetime.now(TZ),
                deadline_at=datetime.now(TZ) + timedelta(seconds=int(deal.round_secs or 20))
            )
            s.add(dr); await s.commit()
            from app.scheduler import schedule_round_end
            await schedule_round_end(deal_id, next_round, int(deal.round_secs or 20))
            _notify = _resolve_notify_round_start(deal.deal_type)
            if _notify:
                await _notify(deal_id, next_round)
            return
        deal.winner_user_id = users[0]
        deal.winner_bid_pct = min_pct
        deal.status = DealStatus.WINNER_ASSIGNED
        await s.commit()
    if deal.deal_type == DealType.SELL:
        await _after_winner_assigned(None, deal_id=deal_id)
    else:
        await _after_winner_assigned_buy(deal_id)

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
    deal.pay_deadline_at = datetime.now(TZ) + timedelta(minutes=pay_minutes)
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
    await schedule_pay_timeout(deal.id, after_minutes=pay_minutes)

async def _after_winner_assigned_buy(deal_id: int):
    from app.bot import create_bot_and_dp
    from app.scheduler import schedule_buy_timeout
    async with async_session() as s:
        d = await s.get(Deal, deal_id)
        if not d:
            return
        d.status = DealStatus.WINNER_ASSIGNED
        mins = int(getattr(d, "pay_mins", 20) or 20)
        d.pay_deadline_at = datetime.now(TZ) + timedelta(minutes=mins)
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
    await schedule_buy_timeout(d.id, after_minutes=mins)

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

```

```python
from __future__ import annotations
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from app.config import settings

scheduler: AsyncIOScheduler | None = None
TZ = ZoneInfo("Europe/Amsterdam")

def get_scheduler() -> AsyncIOScheduler:
    global scheduler
    if scheduler: return scheduler
    scheduler = AsyncIOScheduler(
        timezone=TZ,
        jobstores={"default": SQLAlchemyJobStore(url=settings.scheduler_db_url)}
    )
    return scheduler

async def schedule_stage1_end(deal_id: int, after_seconds: int):
    get_scheduler().add_job(
        "app.workers.auction:end_stage1", "date",
        run_date=datetime.now(TZ)+timedelta(seconds=after_seconds),
        args=[deal_id], id=f"deal:{deal_id}:stage1", replace_existing=True
    )

async def schedule_round_end(deal_id: int, round_number: int, after_seconds: int):
    get_scheduler().add_job(
        "app.workers.auction:end_round", "date",
        run_date=datetime.now(TZ)+timedelta(seconds=after_seconds),
        args=[deal_id, round_number], id=f"deal:{deal_id}:round:{round_number}",
        replace_existing=True
    )

async def schedule_pay_timeout(deal_id: int, after_minutes: int):
    get_scheduler().add_job(
        "app.workers.auction:pay_timeout", "date",
        run_date=datetime.now(TZ)+timedelta(minutes=after_minutes),
        args=[deal_id], id=f"deal:{deal_id}:pay", replace_existing=True
    )

async def schedule_trader_confirm_window(deal_id: int, after_minutes: int = 10):
    get_scheduler().add_job(
        "app.workers.auction:trader_confirm_timeout", "date",
        run_date=datetime.now(TZ)+timedelta(minutes=after_minutes),
        args=[deal_id], id=f"deal:{deal_id}:confirm", replace_existing=True
    )

async def schedule_buy_timeout(deal_id: int, after_minutes: int):
    get_scheduler().add_job(
        "app.workers.auction:buy_pay_timeout", "date",
        run_date=datetime.now(TZ)+timedelta(minutes=after_minutes),
        args=[deal_id], id=f"deal:{deal_id}:buy_pay", replace_existing=True
    )

```

---

**User**

продолжай

---

**Cursor**

I'll update `app/bot.py` to import and include the new BUY routers, then lint the file.

```python
from aiogram import Bot, Dispatcher
from app.handlers.common import router as common_router
from app.handlers.trader import router as trader_router
from app.handlers.admin import router as admin_router
from app.handlers.admin_settings import router as admin_settings_router
from app.handlers.admin_deals_sell import router as admin_deals_sell_router
from app.handlers.sell_auction import router as sell_auction_router
from app.handlers.sell_winner import router as sell_winner_router
from app.handlers.buy_master import router as buy_master_router
from app.handlers.buy_auction import router as buy_auction_router
from app.handlers.buy_winner import router as buy_winner_router


def _include_buy_router_if_exists(dp):
    import importlib
    candidates = (
        ("app.handlers.admin_deals", "router"),
        ("app.handlers.admin_deals_buy", "router"),
        ("app.handlers.buy_master", "router"),
        ("app.handlers.auction_buy", "router"),
    )
    for modname, attr in candidates:
        try:
            mod = importlib.import_module(modname)
            r = getattr(mod, attr, None)
            if r:
                dp.include_router(r)
                break
        except Exception:
            continue


def create_bot_and_dp(bot_token: str):
    bot = Bot(token=(bot_token or ""), parse_mode="HTML")
    dp = Dispatcher()
    dp.include_router(common_router)
    dp.include_router(admin_router)
    dp.include_router(admin_settings_router)
    dp.include_router(admin_deals_sell_router)
    dp.include_router(sell_auction_router)
    dp.include_router(sell_winner_router)
    dp.include_router(buy_master_router)
    dp.include_router(buy_auction_router)
    dp.include_router(buy_winner_router)
    dp.include_router(trader_router)

    _include_buy_router_if_exists(dp)

    return bot, dp

```

- Готово: удалил старую кнопку в `app/handlers/admin.py`, добавил `admin:buy_new` и новый обработчик.
- Добавил новые файлы: `app/handlers/buy_master.py`, `app/handlers/buy_auction.py`, `app/handlers/buy_winner.py`.
- Обновил воркер `app/workers/auction.py`: разветвление SELL/BUY, уведомления BUY, `_after_winner_assigned_buy`, `buy_pay_timeout`.
- Добавил планировщик `schedule_buy_timeout` в `app/scheduler.py`.
- Подключил BUY-роутеры в `app/bot.py`.

Что дальше:
- Перезапустите бота и проверьте кнопку в админ-панели.
- Введите: команды "Панель администратора" → "Торги → Покупка (мастер)".

Если что-то упадёт — кину быстрый фикс.

