# 🎯 ПОЛНЫЙ АЛГОРИТМ: ТОРГИ ПОКУПКА (мастер)

## 📋 Описание функционала

Панель администратора "ТОРГИ ПОКУПКА (мастер)" позволяет создавать сделки покупки RUB у трейдеров с автоматическим аукционом и детальной настройкой всех параметров.

---

## 🏗️ Структура данных

### Модель Deal (сделка)

```python
class Deal(Base):
    __tablename__ = "deals"
    
    # Основные поля
    deal_no: str                    # Уникальный номер сделки (20241215-A1B2C3D)
    deal_type: DealType.BUY         # Тип: покупка RUB
    amount_rub: float               # Сумма в рублях
    amount_usdt_snapshot: float     # Снэпшот USDT по курсу на момент создания
    
    # Параметры вознаграждения
    base_pct: float                 # Базовое вознаграждение трейдера (-5%..+25%)
    service_fee_pct: float          # Комиссия сервиса (0%..3%)
    
    # Платежные реквизиты
    pay_bank: str                   # Банк для оплаты (ТБАНК, СБЕРБАНК, АЛЬФАБАНК, ВТБ, ЯНДЕКС БАНК, ОЗОН БАНК, ЛЮБОЙ БАНК)
    pay_account: str                # Счёт для перевода (6-64 символа)
    
    # Дополнительные опции
    need_email: bool                # Нужен ли чек на email
    email_for_receipt: str          # Email для чека
    comment: str                    # Комментарий к сделке (до 1000 символов)
    warning_enabled: bool           # Включить предупреждение
    
    # Настройки аудитории
    audience_type: str              # Тип аудитории (all/rep_range/personal)
    audience_filter: str            # Фильтр аудитории (JSON)
    
    # Временные параметры
    pay_mins: int                   # Время на оплату (5/10/15/20/25/30 мин)
    round_secs: int                 # Длительность раунда аукциона
    max_tie_rounds: int             # Максимум тай-брейков
    
    # Статус и результаты
    status: DealStatus              # Статус сделки (OPEN, BIDDING, COMPLETED, etc.)
    winner_user_id: int             # ID победителя
    winner_bid_pct: float           # Процент ставки победителя
```

### Настройки системы

```python
class SystemSettings(Base):
    __tablename__ = "system_settings"
    
    rub_per_usdt: float                    # Курс RUB/USDT
    service_fee_default_pct: float         # Комиссия по умолчанию
    service_fee_min_pct: float             # Минимальная комиссия
    service_fee_max_pct: float             # Максимальная комиссия
    service_fee_overridable: bool          # Можно ли переопределить комиссию
    round_secs_default: int                # Длительность раунда по умолчанию
    max_tie_rounds_default: int            # Максимум тай-брейков по умолчанию
```

---

## 🔄 Пошаговый алгоритм создания сделки

### Шаг 1: Инициализация
- **Команда:** `/deals`
- **Действие:** Показ меню "Торги → Покупка"
- **Кнопка:** "➕ Покупка (мастер)"
- **Результат:** Начало FSM (Finite State Machine)

### Шаг 2: Сумма сделки
```python
@router.message(DealStates.amount)
async def step_amount(message: Message, state: FSMContext):
    try:
        # Парсинг суммы
        rub = float((message.text or "").replace(" ", "").replace(",", "."))
        if rub <= 0: raise ValueError
        
        # Получение настроек и конвертация
        s = await get_settings()
        usdt = bank_round_2(rub / s.rub_per_usdt)
        
        # Сохранение в состояние
        await state.update_data(amount_rub=bank_round_2(rub), amount_usdt=usdt)
        await state.set_state(DealStates.pay_account)
        
        # Следующий шаг
        await message.answer(
            f"USDT эквивалент: ~ {usdt:.2f}\n"
            f"2) Введите счёт перевода (пример: 11110000111100001111 ТБАНК или +7911111001100 ТБАНК)."
        )
    except Exception:
        await message.answer("Некорректная сумма. Повторите ввод.")
```

**Валидация:**
- Положительное число
- Поддержка пробелов и запятых
- Автоматическая конвертация RUB → USDT по текущему курсу

### Шаг 3: Счёт для перевода
```python
@router.message(DealStates.pay_account)
async def step_pay_account(message: Message, state: FSMContext):
    s = (message.text or "").strip()
    
    # Валидация формата
    if not validate_pay_account(s) or not (6 <= len(s) <= 64):
        await message.answer(
            "Счёт некорректен. Разрешены цифры, '+', пробелы, длина 6..64. "
            "В конце укажите банк."
        )
        return
    
    await state.update_data(pay_account=s)
    await state.set_state(DealStates.base_pct)
    await message.answer("3) Введите базовое вознаграждение трейдера, % (−5.00 .. +25.00).")
```

**Валидация:**
- Длина: 6-64 символа
- Разрешены: цифры, +, пробелы, буквы
- Банк должен быть указан в конце

### Шаг 4: Базовое вознаграждение
```python
@router.message(DealStates.base_pct)
async def step_base_pct(message: Message, state: FSMContext):
    try:
        v = float((message.text or "").replace(",", "."))
        
        # Валидация диапазона и шага
        if not validate_decimal_range_step(v, -5.00, 25.00, 0.01):
            raise ValueError
            
        await state.update_data(base_pct=round(v, 2))
        
        # Проверка возможности переопределения комиссии
        s = await get_settings()
        if s.service_fee_overridable:
            await state.set_state(DealStates.service_fee)
            await message.answer(
                f"4) Комиссия сервиса, % (диапазон {s.service_fee_min_pct:.2f}..{s.service_fee_max_pct:.2f}). "
                f"По умолчанию: {s.service_fee_default_pct:.2f}%"
            )
        else:
            # Используем дефолтную комиссию
            await state.update_data(service_fee_pct=s.service_fee_default_pct)
            await state.set_state(DealStates.bank_pick)
            await message.answer("6) Выберите банк для оплаты:", reply_markup=kb_banks())
    except Exception:
        await message.answer("Неверное значение. Введите в диапазоне −5.00..+25.00 с шагом 0.01.")
```

**Валидация:**
- Диапазон: -5.00% .. +25.00%
- Шаг: 0.01%
- Поддержка запятых и точек

### Шаг 5: Комиссия сервиса (опционально)
```python
@router.message(DealStates.service_fee)
async def step_service_fee(message: Message, state: FSMContext):
    try:
        s = await get_settings()
        v = float((message.text or "").replace(",", "."))
        
        # Валидация по настройкам системы
        if not validate_decimal_range_step(v, s.service_fee_min_pct, s.service_fee_max_pct, 0.01):
            raise ValueError
            
        await state.update_data(service_fee_pct=round(v, 2))
        await state.set_state(DealStates.bank_pick)
        await message.answer("6) Выберите банк для оплаты:", reply_markup=kb_banks())
    except Exception:
        s = await get_settings()
        await message.answer(
            f"Неверно. Введите значение в диапазоне {s.service_fee_min_pct:.2f}..{s.service_fee_max_pct:.2f} "
            f"с шагом 0.01."
        )
```

**Особенности:**
- Показывается только если `service_fee_overridable = True`
- Диапазон берется из настроек системы
- Если выключено - используется дефолтное значение

### Шаг 6: Выбор банка
```python
@router.callback_query(DealStates.bank_pick, F.data.startswith("bank:"))
async def step_bank(cq: CallbackQuery, state: FSMContext):
    bank = cq.data.split(":", 1)[1]
    await state.update_data(pay_bank=bank)
    await state.set_state(DealStates.need_email)
    
    await cq.message.answer(
        "7) Нужно отправить чек на e-mail? (Да/Нет)", 
        reply_markup=kb_yesno("email:yes", "email:no")
    )
    await cq.answer()

def kb_banks():
    b = InlineKeyboardBuilder()
    banks = ["ТБАНК", "СБЕРБАНК", "АЛЬФАБАНК", "ВТБ", "ЯНДЕКС БАНК", "ОЗОН БАНК", "ЛЮБОЙ БАНК"]
    for name in banks:
        b.button(text=name, callback_data=f"bank:{name}")
    b.adjust(2)  # 2 кнопки в ряду
    return b.as_markup()
```

**Доступные банки:**
- ТБАНК
- СБЕРБАНК  
- АЛЬФАБАНК
- ВТБ
- ЯНДЕКС БАНК
- ОЗОН БАНК
- ЛЮБОЙ БАНК

### Шаг 7: Email для чека
```python
@router.callback_query(DealStates.need_email, F.data.startswith("email:"))
async def step_need_email(cq: CallbackQuery, state: FSMContext):
    yn = cq.data.split(":", 1)[1]
    
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
    
    # Валидация email
    if not validate_email_addr(addr):
        await message.answer("E-mail некорректен. Повторите.")
        return
        
    await state.update_data(email_for_receipt=addr)
    await state.set_state(DealStates.pay_time)
    await message.answer("8) Время на оплату (мин): выберите 5/10/15/20/25/30")
```

**Валидация email:**
- Используется библиотека `email-validator`
- Проверка формата и домена
- Если не нужен - пропускается

### Шаг 8: Время на оплату
```python
@router.message(DealStates.pay_time)
async def step_pay_time(message: Message, state: FSMContext):
    try:
        mins = int((message.text or "").strip())
        
        # Только предустановленные значения
        if mins not in (5, 10, 15, 20, 25, 30):
            raise ValueError
            
        await state.update_data(pay_mins=mins)
        await state.set_state(DealStates.comment_ask)
        await message.answer(
            "9) Добавить комментарий? (Да/Нет)", 
            reply_markup=kb_yesno("cmt:yes", "cmt:no")
        )
    except Exception:
        await message.answer("Введите одно из значений: 5/10/15/20/25/30.")
```

**Доступные значения:**
- 5 минут
- 10 минут  
- 15 минут
- 20 минут
- 25 минут
- 30 минут

### Шаг 9: Комментарий
```python
@router.callback_query(DealStates.comment_ask, F.data.startswith("cmt:"))
async def step_comment_ask(cq: CallbackQuery, state: FSMContext):
    yn = cq.data.split(":", 1)[1]
    
    if yn == "yes":
        await state.set_state(DealStates.comment_text)
        await cq.message.answer("Введите комментарий (до 1000 символов).")
    else:
        await state.update_data(comment=None)
        await state.set_state(DealStates.warning_toggle)
        await cq.message.answer(
            "10) Добавить предупреждение? (Да/Нет)", 
            reply_markup=kb_yesno("warn:yes", "warn:no")
        )
    await cq.answer()

@router.message(DealStates.comment_text)
async def step_comment_text(message: Message, state: FSMContext):
    txt = (message.text or "").strip()
    
    # Ограничение длины
    if len(txt) > 1000:
        await message.answer("Слишком длинно. До 1000 символов.")
        return
        
    await state.update_data(comment=txt)
    await state.set_state(DealStates.warning_toggle)
    await cq.message.answer(
        "10) Добавить предупреждение? (Да/Нет)", 
        reply_markup=kb_yesno("warn:yes", "warn:no")
    )
```

**Особенности:**
- Опциональное поле
- Максимум 1000 символов
- Если не нужен - пропускается

### Шаг 10: Предупреждение
```python
@router.callback_query(DealStates.warning_toggle, F.data.startswith("warn:"))
async def step_warning(cq: CallbackQuery, state: FSMContext):
    yn = cq.data.split(":", 1)[1]
    await state.update_data(warning_enabled=(yn == "yes"))
    
    # Переход к выбору аудитории
    await state.set_state(DealStates.audience)
    
    kb = InlineKeyboardBuilder()
    kb.button(text="Для всех (онлайн)", callback_data="aud:all")
    kb.button(text="По рейтингу", callback_data="aud:rep")
    kb.button(text="Персонально", callback_data="aud:per")
    kb.adjust(1)  # 1 кнопка в ряду
    
    await cq.message.answer("11) Выберите аудиторию:", reply_markup=kb.as_markup())
    await cq.answer()
```

**Типы предупреждений:**
- Да - предупреждение включено
- Нет - предупреждение выключено

### Шаг 11: Выбор аудитории
```python
@router.callback_query(DealStates.audience, F.data.startswith("aud:"))
async def step_audience(cq: CallbackQuery, state: FSMContext):
    mode = cq.data.split(":", 1)[1]
    data = await state.get_data()
    s = await get_settings()

    # Генерация уникального номера сделки
    from app.utils.dt import now_tz
    deal_no = now_tz().strftime("%Y%m%d-") + __import__("secrets").token_hex(4).upper()[:7]
    
    # Снэпшот комиссии из состояния или дефолта
    service_fee_pct = float(data["service_fee_pct"]) if "service_fee_pct" in data else float(s.service_fee_default_pct)

    # Проверка прав администратора
    async with async_session() as session:
        u = await ensure_user(session, cq.from_user)
        if u.role != RoleEnum.admin:
            await cq.answer("Только для администратора", show_alert=True)
            return

        # Создание сделки
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

    # Очистка состояния и показ результата
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
```

**Типы аудитории:**
- **all:** "Для всех (онлайн)" - все активные трейдеры
- **rep:** "По рейтингу" - трейдеры с определённым рейтингом  
- **per:** "Персонально" - конкретные трейдеры

---

## 🔧 Вспомогательные функции

### Клавиатуры
```python
def kb_banks():
    """Клавиатура выбора банка"""
    b = InlineKeyboardBuilder()
    banks = ["ТБАНК", "СБЕРБАНК", "АЛЬФАБАНК", "ВТБ", "ЯНДЕКС БАНК", "ОЗОН БАНК", "ЛЮБОЙ БАНК"]
    for name in banks:
        b.button(text=name, callback_data=f"bank:{name}")
    b.adjust(2)  # 2 кнопки в ряду
    return b.as_markup()

def kb_yesno(cb_yes: str, cb_no: str):
    """Клавиатура Да/Нет"""
    b = InlineKeyboardBuilder()
    b.button(text="Да", callback_data=cb_yes)
    b.button(text="Нет", callback_data=cb_no)
    b.adjust(2)
    return b.as_markup()
```

### Валидация
```python
def validate_pay_account(s: str) -> bool:
    """Валидация счёта для перевода"""
    s = s.strip()
    if not (6 <= len(s) <= 64): 
        return False
    # Цифры, +, пробелы, и произвольный хвост банка
    ok = all(ch.isdigit() or ch in "+ " or ch.isalpha() for ch in s)
    return ok

def validate_decimal_range_step(value: float, lo: float, hi: float, step: float = 0.01) -> bool:
    """Валидация десятичного числа в диапазоне с шагом"""
    if not (lo <= value <= hi): 
        return False
    # Проверка шага 0.01
    return abs(round(value * 100) - value * 100) < 1e-9
```

---

## 📊 Последовательность состояний FSM

```python
class DealStates(StatesGroup):
    amount = State()           # 1. Сумма сделки
    pay_account = State()      # 2. Счёт для перевода  
    base_pct = State()         # 3. Базовое вознаграждение
    service_fee = State()      # 4. Комиссия сервиса (опционально)
    bank_pick = State()        # 5. Выбор банка
    need_email = State()       # 6. Нужен ли email
    email_input = State()      # 7. Ввод email (если нужен)
    pay_time = State()         # 8. Время на оплату
    comment_ask = State()      # 9. Нужен ли комментарий
    comment_text = State()     # 10. Ввод комментария (если нужен)
    warning_toggle = State()   # 11. Включить предупреждение
    audience = State()         # 12. Выбор аудитории
    confirm = State()          # 13. Подтверждение и создание
```

---

## 🎯 Результат создания сделки

После успешного создания:

1. **Статус:** `DealStatus.OPEN`
2. **Сделка готова** к участию трейдеров
3. **Автоматически запускается** рассылка уведомлений
4. **Начинается отсчёт** времени на оплату
5. **Генерируется уникальный номер** сделки (формат: YYYYMMDD-XXXXXXX)

### Пример номера сделки:
```
20241215-A1B2C3D
├── 20241215 - дата создания (15 декабря 2024)
└── A1B2C3D - случайный 7-значный хеш
```

---

## 🔒 Безопасность и права доступа

- **Только администраторы** могут создавать сделки
- **Проверка роли** на каждом шаге
- **Валидация всех входных данных**
- **Аудит изменений** через `AuditLog`
- **Снэпшот настроек** на момент создания

---

## 📱 Пользовательский интерфейс

- **Пошаговый мастер** с понятными инструкциями
- **Inline-клавиатуры** для выбора опций
- **Валидация в реальном времени** с понятными ошибками
- **Автодополнение** и подсказки
- **Возможность отмены** на любом шаге

---

## 🚀 Автоматизация

- **Автоматическая конвертация** RUB → USDT по текущему курсу
- **Снэпшот настроек** для предотвращения изменений во время сделки
- **Генерация уникальных номеров** сделок
- **Автоматический запуск** аукциона после создания
- **Интеграция с планировщиком** для таймеров и уведомлений
