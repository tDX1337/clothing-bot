import asyncio
import os
import sqlite3
from typing import Any, Awaitable, Callable, Dict

from aiogram import Bot, Dispatcher, F, BaseMiddleware
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ReplyKeyboardMarkup,
    KeyboardButton,
)


# =========================================================
# НАСТРОЙКИ
# =========================================================

TOKEN = os.getenv("BOT_TOKEN")

if not TOKEN:
    raise RuntimeError("Не найден BOT_TOKEN")

DB_NAME = "shop.db"
ADMIN_ID = 5529220398


# =========================================================
# БАЗА ДАННЫХ
# =========================================================

def get_db():
    conn = sqlite3.connect(DB_NAME)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    conn = get_db()
    cur = conn.cursor()

    # -----------------------------------------------------
    # ПРОВЕРКА СТАРОЙ СТРУКТУРЫ
    # -----------------------------------------------------

    cur.execute("PRAGMA table_info(products)")
    columns = [row[1] for row in cur.fetchall()]

    if "size" in columns:
        cur.execute("DROP TABLE IF EXISTS sales")
        cur.execute("DROP TABLE IF EXISTS product_sizes")
        cur.execute("DROP TABLE IF EXISTS products")

    # -----------------------------------------------------
    # PRODUCTS
    # -----------------------------------------------------

    cur.execute("""
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            purchase_price REAL NOT NULL DEFAULT 0,
            sale_price REAL NOT NULL DEFAULT 0
        )
    """)

    # -----------------------------------------------------
    # PRODUCT SIZES
    # -----------------------------------------------------

    cur.execute("""
        CREATE TABLE IF NOT EXISTS product_sizes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id INTEGER NOT NULL,
            size TEXT NOT NULL,
            stock INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY(product_id) REFERENCES products(id)
                ON DELETE CASCADE
        )
    """)

    # -----------------------------------------------------
    # SALES
    # -----------------------------------------------------

    cur.execute("""
        CREATE TABLE IF NOT EXISTS sales (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id INTEGER,
            size_id INTEGER,
            product_name TEXT NOT NULL,
            size TEXT NOT NULL,
            quantity INTEGER NOT NULL,
            sale_price REAL NOT NULL,
            purchase_price REAL NOT NULL,
            profit REAL NOT NULL,
            payment_method TEXT NOT NULL DEFAULT 'cash',
            status TEXT NOT NULL DEFAULT 'completed',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(product_id) REFERENCES products(id)
                ON DELETE SET NULL
        )
    """)

    # Миграция старой sales
    cur.execute("PRAGMA table_info(sales)")
    sales_columns = [row[1] for row in cur.fetchall()]

    if "payment_method" not in sales_columns:
        cur.execute("""
            ALTER TABLE sales
            ADD COLUMN payment_method TEXT NOT NULL DEFAULT 'cash'
        """)

    if "status" not in sales_columns:
        cur.execute("""
            ALTER TABLE sales
            ADD COLUMN status TEXT NOT NULL DEFAULT 'completed'
        """)

    if "size_id" not in sales_columns:
        cur.execute("""
            ALTER TABLE sales
            ADD COLUMN size_id INTEGER
        """)

    # -----------------------------------------------------
    # АВТОМАТИЧЕСКОЕ ВОССТАНОВЛЕНИЕ size_id
    # ДЛЯ СТАРЫХ ПРОДАЖ
    # -----------------------------------------------------

    cur.execute("""
        UPDATE sales
        SET size_id = (
            SELECT ps.id
            FROM product_sizes ps
            WHERE ps.product_id = sales.product_id
              AND LOWER(TRIM(ps.size)) = LOWER(TRIM(sales.size))
            LIMIT 1
        )
        WHERE size_id IS NULL
          AND product_id IS NOT NULL
    """)

    # -----------------------------------------------------
    # ALLOWED USERS
    # -----------------------------------------------------

    cur.execute("""
        CREATE TABLE IF NOT EXISTS allowed_users (
            user_id INTEGER PRIMARY KEY,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # -----------------------------------------------------
    # EXPENSES
    # -----------------------------------------------------

    cur.execute("""
        CREATE TABLE IF NOT EXISTS expenses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            description TEXT NOT NULL,
            amount REAL NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # -----------------------------------------------------
    # РУЧНЫЕ ЗНАЧЕНИЯ В РАЗДЕЛЕ "ПРИБЫЛЬ"
    # -----------------------------------------------------
    # Здесь хранятся только ручные значения, которые задаёт
    # создатель. Если ключ отсутствует, используется автомат
    # расчёт из sales/expenses.
    cur.execute("""
        CREATE TABLE IF NOT EXISTS profit_overrides (
            key TEXT PRIMARY KEY,
            value REAL NOT NULL
        )
    """)

    conn.commit()
    conn.close()


init_db()


# =========================================================
# FSM
# =========================================================

class AddProduct(StatesGroup):
    name = State()
    purchase_price = State()
    sale_price = State()
    sizes = State()
    stock = State()


class ReplenishStock(StatesGroup):
    quantity = State()


class Sale(StatesGroup):
    quantity = State()
    actual_price = State()


class EditProduct(StatesGroup):
    name = State()
    purchase_price = State()
    sale_price = State()


class Access(StatesGroup):
    user_id = State()


class Expense(StatesGroup):
    description = State()
    amount = State()


class EditExpense(StatesGroup):
    description = State()
    amount = State()


class EditSale(StatesGroup):
    sale_price = State()
    purchase_price = State()


class ProfitSettingsEdit(StatesGroup):
    value = State()


# =========================================================
# BOT
# =========================================================

bot = Bot(
    token=TOKEN,
    default=DefaultBotProperties(
        parse_mode=ParseMode.HTML
    )
)

dp = Dispatcher()


# =========================================================
# ACCESS
# =========================================================

class AccessMiddleware(BaseMiddleware):

    async def __call__(
        self,
        handler: Callable[[Any, Dict[str, Any]], Awaitable[Any]],
        event: Any,
        data: Dict[str, Any]
    ) -> Any:

        user = data.get("event_from_user")

        if not user:
            return await handler(event, data)

        user_id = user.id

        if user_id == ADMIN_ID:
            return await handler(event, data)

        conn = get_db()
        cur = conn.cursor()

        cur.execute(
            "SELECT user_id FROM allowed_users WHERE user_id = ?",
            (user_id,)
        )

        allowed = cur.fetchone()

        conn.close()

        if allowed:
            return await handler(event, data)

        if isinstance(event, Message):
            await event.answer(
                "⛔ <b>У вас нет доступа к боту.</b>\n\n"
                "Обратитесь к администратору."
            )

        elif isinstance(event, CallbackQuery):
            await event.answer(
                "⛔ У вас нет доступа.",
                show_alert=True
            )

        return


dp.message.outer_middleware(AccessMiddleware())
dp.callback_query.outer_middleware(AccessMiddleware())


# =========================================================
# КЛАВИАТУРА
# =========================================================

def get_menu(user_id: int) -> ReplyKeyboardMarkup:

    rows = [
        [
            KeyboardButton(text="📦 Наличие товаров"),
            KeyboardButton(text="🛒 Продажа"),
        ],
        [
            KeyboardButton(text="📊 Статистика"),
            KeyboardButton(text="💰 Прибыль"),
        ],
        [
            KeyboardButton(text="➕ Добавить товар"),
            KeyboardButton(text="📥 Пополнить остаток"),
        ],
        [
            KeyboardButton(text="✏️ Изменить товар"),
            KeyboardButton(text="🗑️ Удалить товар"),
        ],
    ]

    if user_id == ADMIN_ID:
        rows.append([
            KeyboardButton(text="🔐 Доступ")
        ])

    return ReplyKeyboardMarkup(
        keyboard=rows,
        resize_keyboard=True
    )


MAIN_MENU_BUTTONS = {
    "📦 Наличие товаров",
    "🛒 Продажа",
    "📊 Статистика",
    "💰 Прибыль",
    "➕ Добавить товар",
    "📥 Пополнить остаток",
    "✏️ Изменить товар",
    "🗑️ Удалить товар",
    "🔐 Доступ",
}


# =========================================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# =========================================================

def money(value: float) -> str:
    return (
        f"{value:,.2f}"
        .replace(",", " ")
        .replace(".", ",")
        + " ₽"
    )


def get_products():
    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        SELECT id, name, purchase_price, sale_price
        FROM products
        ORDER BY id DESC
    """)

    rows = cur.fetchall()
    conn.close()

    return rows


def get_product(product_id: int):
    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        SELECT id, name, purchase_price, sale_price
        FROM products
        WHERE id = ?
    """, (product_id,))

    row = cur.fetchone()
    conn.close()

    return row


def get_sizes(product_id: int):
    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        SELECT id, size, stock
        FROM product_sizes
        WHERE product_id = ?
        ORDER BY id
    """, (product_id,))

    rows = cur.fetchall()
    conn.close()

    return rows


def get_size(size_id: int):
    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        SELECT id, product_id, size, stock
        FROM product_sizes
        WHERE id = ?
    """, (size_id,))

    row = cur.fetchone()
    conn.close()

    return row


# =========================================================
# START
# =========================================================

@dp.message(CommandStart())
async def start(message: Message, state: FSMContext):

    await state.clear()

    await message.answer(
        "👋 <b>Добро пожаловать!</b>\n\n"
        "Выберите действие:",
        reply_markup=get_menu(message.from_user.id)
    )


# =========================================================
# ГЛОБАЛЬНАЯ НАВИГАЦИЯ
# =========================================================

@dp.message(F.text.in_(MAIN_MENU_BUTTONS))
async def global_menu_navigation(
    message: Message,
    state: FSMContext
):

    await state.clear()

    text = message.text

    if text == "📦 Наличие товаров":
        await show_stock(message)

    elif text == "🛒 Продажа":
        await sale_start(message)

    elif text == "📊 Статистика":
        await show_statistics(message)

    elif text == "💰 Прибыль":
        await show_profit(message)

    elif text == "➕ Добавить товар":
        await add_product_start(message, state)

    elif text == "📥 Пополнить остаток":
        await replenish_start(message)

    elif text == "✏️ Изменить товар":
        await edit_product_start(message)

    elif text == "🗑️ Удалить товар":
        await delete_product_start(message)

    elif text == "🔐 Доступ":
        if message.from_user.id == ADMIN_ID:
            await access_start(message, state)


# =========================================================
# НАЛИЧИЕ
# =========================================================

async def show_stock(message: Message):

    products = get_products()

    text = "📦 <b>НАЛИЧИЕ ТОВАРОВ</b>\n\n"
    visible_products = 0

    for product_id, name, purchase_price, sale_price in products:
        sizes = get_sizes(product_id)
        available_sizes = [
            (size_id, size, stock)
            for size_id, size, stock in sizes
            if stock > 0
        ]

        # Товары с нулевым остатком полностью скрываем из наличия.
        if not available_sizes:
            continue

        visible_products += 1
        total_stock = sum(stock for _, _, stock in available_sizes)

        text += (
            f"🛍 <b>{name}</b>\n"
            f"Закупка: {money(purchase_price)}\n"
            f"Продажа: {money(sale_price)}\n"
            f"Всего: <b>{total_stock} шт.</b>\n"
        )

        for _, size, stock in available_sizes:
            text += f"   • {size}: <b>{stock} шт.</b>\n"

        text += "\n"

    if visible_products == 0:
        await message.answer(
            "📦 <b>НАЛИЧИЕ ТОВАРОВ</b>\n\n"
            "Сейчас товаров в наличии нет.\n\n"
            "Пополните остаток через кнопку «📥 Пополнить остаток»."
        )
        return

    await message.answer(text)


# =========================================================
# ДОБАВЛЕНИЕ ТОВАРА
# =========================================================

async def add_product_start(
    message: Message,
    state: FSMContext
):

    await state.clear()
    await state.set_state(AddProduct.name)

    await message.answer(
        "➕ <b>Добавление товара</b>\n\n"
        "Введите название товара:"
    )


@dp.message(AddProduct.name)
async def add_product_name(
    message: Message,
    state: FSMContext
):

    name = message.text.strip()

    if not name:
        await message.answer("Введите название товара.")
        return

    await state.update_data(name=name)
    await state.set_state(AddProduct.purchase_price)

    await message.answer(
        "Введите закупочную цену (₽):"
    )


@dp.message(AddProduct.purchase_price)
async def add_product_purchase(
    message: Message,
    state: FSMContext
):

    try:
        price = float(message.text.replace(",", "."))
        if price < 0:
            raise ValueError
    except ValueError:
        await message.answer(
            "❌ Введите корректную цену.\n"
            "Например: 1500"
        )
        return

    await state.update_data(purchase_price=price)
    await state.set_state(AddProduct.sale_price)

    await message.answer(
        "Введите цену продажи (₽):"
    )


@dp.message(AddProduct.sale_price)
async def add_product_sale(
    message: Message,
    state: FSMContext
):

    try:
        price = float(message.text.replace(",", "."))
        if price < 0:
            raise ValueError
    except ValueError:
        await message.answer(
            "❌ Введите корректную цену.\n"
            "Например: 2490"
        )
        return

    await state.update_data(sale_price=price)
    await state.set_state(AddProduct.sizes)

    await message.answer(
        "Введите размеры через запятую.\n\n"
        "Например:\n"
        "<code>S, M, L, XL</code>\n\n"
        "Если размер не нужен:\n"
        "<code>ONE SIZE</code>"
    )


@dp.message(AddProduct.sizes)
async def add_product_sizes(
    message: Message,
    state: FSMContext
):

    sizes = [
        item.strip()
        for item in message.text.split(",")
        if item.strip()
    ]

    if not sizes:
        await message.answer(
            "❌ Укажите хотя бы один размер."
        )
        return

    await state.update_data(
        sizes=sizes,
        current_size_index=0,
        stocks=[]
    )

    await state.set_state(AddProduct.stock)

    await message.answer(
        f"Введите остаток для размера "
        f"<b>{sizes[0]}</b>:"
    )


@dp.message(AddProduct.stock)
async def add_product_stock(
    message: Message,
    state: FSMContext
):

    try:
        stock = int(message.text)

        if stock < 0:
            raise ValueError

    except ValueError:
        await message.answer(
            "❌ Введите целое число.\n"
            "Например: 20"
        )
        return

    data = await state.get_data()

    sizes = data["sizes"]
    index = data["current_size_index"]
    stocks = data["stocks"]

    stocks.append(stock)
    index += 1

    if index < len(sizes):

        await state.update_data(
            current_size_index=index,
            stocks=stocks
        )

        await message.answer(
            f"Введите остаток для размера "
            f"<b>{sizes[index]}</b>:"
        )

        return

    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO products (
            name,
            purchase_price,
            sale_price
        )
        VALUES (?, ?, ?)
    """, (
        data["name"],
        data["purchase_price"],
        data["sale_price"]
    ))

    product_id = cur.lastrowid

    for size, size_stock in zip(sizes, stocks):

        cur.execute("""
            INSERT INTO product_sizes (
                product_id,
                size,
                stock
            )
            VALUES (?, ?, ?)
        """, (
            product_id,
            size,
            size_stock
        ))

    conn.commit()
    conn.close()

    await state.clear()

    await message.answer(
        "✅ <b>Товар добавлен!</b>\n\n"
        f"🛍 {data['name']}\n"
        f"Закупка: {money(data['purchase_price'])}\n"
        f"Продажа: {money(data['sale_price'])}\n\n"
        "Остатки сохранены.",
        reply_markup=get_menu(message.from_user.id)
    )


# =========================================================
# ПОПОЛНЕНИЕ
# =========================================================

async def replenish_start(message: Message):

    products = get_products()

    if not products:
        await message.answer("📥 Товаров пока нет.")
        return

    buttons = []

    for product_id, name, _, _ in products:
        buttons.append([
            InlineKeyboardButton(
                text=name,
                callback_data=f"replenish_product:{product_id}"
            )
        ])

    await message.answer(
        "📥 <b>Пополнение остатка</b>\n\n"
        "Выберите товар:",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=buttons
        )
    )


@dp.callback_query(F.data.startswith("replenish_product:"))
async def replenish_product(callback: CallbackQuery):

    product_id = int(callback.data.split(":")[1])
    product = get_product(product_id)

    if not product:
        await callback.answer(
            "Товар не найден.",
            show_alert=True
        )
        return

    sizes = get_sizes(product_id)

    buttons = []

    for size_id, size, stock in sizes:
        buttons.append([
            InlineKeyboardButton(
                text=f"{size} — {stock} шт.",
                callback_data=f"replenish_size:{size_id}"
            )
        ])

    await callback.message.edit_text(
        f"📥 <b>{product[1]}</b>\n\n"
        "Выберите размер:",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=buttons
        )
    )

    await callback.answer()


@dp.callback_query(F.data.startswith("replenish_size:"))
async def replenish_size(
    callback: CallbackQuery,
    state: FSMContext
):

    size_id = int(callback.data.split(":")[1])
    size = get_size(size_id)

    if not size:
        await callback.answer(
            "Размер не найден.",
            show_alert=True
        )
        return

    await state.update_data(
        replenish_size_id=size_id
    )

    await state.set_state(ReplenishStock.quantity)

    await callback.message.edit_text(
        f"📥 Размер: <b>{size[2]}</b>\n\n"
        f"Сейчас на складе: <b>{size[3]} шт.</b>\n\n"
        "Введите количество, которое добавить:"
    )

    await callback.answer()


@dp.message(ReplenishStock.quantity)
async def replenish_quantity(
    message: Message,
    state: FSMContext
):

    try:
        quantity = int(message.text)

        if quantity <= 0:
            raise ValueError

    except ValueError:
        await message.answer(
            "❌ Введите положительное целое число."
        )
        return

    data = await state.get_data()
    size_id = data["replenish_size_id"]

    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        UPDATE product_sizes
        SET stock = stock + ?
        WHERE id = ?
    """, (quantity, size_id))

    cur.execute("""
        SELECT size, stock
        FROM product_sizes
        WHERE id = ?
    """, (size_id,))

    row = cur.fetchone()

    conn.commit()
    conn.close()

    await state.clear()

    if not row:
        await message.answer(
            "❌ Размер не найден.",
            reply_markup=get_menu(message.from_user.id)
        )
        return

    await message.answer(
        "✅ <b>Остаток пополнен!</b>\n\n"
        f"Размер: <b>{row[0]}</b>\n"
        f"Добавлено: <b>{quantity} шт.</b>\n"
        f"Теперь на складе: <b>{row[1]} шт.</b>",
        reply_markup=get_menu(message.from_user.id)
    )


# =========================================================
# ПРОДАЖА
# =========================================================

async def sale_start(message: Message):

    products = get_products()
    buttons = []

    for product_id, name, _, _ in products:
        sizes = get_sizes(product_id)
        if any(stock > 0 for _, _, stock in sizes):
            buttons.append([
                InlineKeyboardButton(
                    text=name,
                    callback_data=f"sale_product:{product_id}"
                )
            ])

    if not buttons:
        await message.answer(
            "🛒 <b>Продажа</b>\n\n"
            "❌ Сейчас товаров в наличии нет."
        )
        return

    await message.answer(
        "🛒 <b>Продажа</b>\n\n"
        "Выберите товар:",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=buttons
        )
    )


@dp.callback_query(F.data.startswith("sale_product:"))
async def sale_product(callback: CallbackQuery):

    product_id = int(callback.data.split(":")[1])
    product = get_product(product_id)

    if not product:
        await callback.answer(
            "Товар не найден.",
            show_alert=True
        )
        return

    sizes = get_sizes(product_id)

    buttons = []

    for size_id, size, stock in sizes:

        if stock <= 0:
            continue

        buttons.append([
            InlineKeyboardButton(
                text=f"{size} — {stock} шт.",
                callback_data=f"sale_size:{size_id}"
            )
        ])

    if not buttons:
        await callback.message.edit_text(
            f"🛍 <b>{product[1]}</b>\n\n"
            "❌ Товара нет в наличии."
        )
        await callback.answer()
        return

    await callback.message.edit_text(
        f"🛍 <b>{product[1]}</b>\n\n"
        "Выберите размер:",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=buttons
        )
    )

    await callback.answer()


@dp.callback_query(F.data.startswith("sale_size:"))
async def sale_size(
    callback: CallbackQuery,
    state: FSMContext
):

    size_id = int(callback.data.split(":")[1])
    size = get_size(size_id)

    if not size:
        await callback.answer(
            "Размер не найден.",
            show_alert=True
        )
        return

    if size[3] <= 0:
        await callback.answer(
            "Товара нет в наличии.",
            show_alert=True
        )
        return

    await state.update_data(
        sale_size_id=size_id
    )

    await state.set_state(Sale.quantity)

    await callback.message.edit_text(
        f"🛒 Размер: <b>{size[2]}</b>\n"
        f"В наличии: <b>{size[3]} шт.</b>\n\n"
        "Введите количество:"
    )

    await callback.answer()


@dp.message(Sale.quantity)
async def sale_quantity(
    message: Message,
    state: FSMContext
):

    try:
        quantity = int(message.text)

        if quantity <= 0:
            raise ValueError

    except ValueError:
        await message.answer(
            "❌ Введите положительное целое число."
        )
        return

    data = await state.get_data()
    size_id = data["sale_size_id"]

    size = get_size(size_id)

    if not size:
        await state.clear()
        await message.answer("❌ Размер не найден.")
        return

    if quantity > size[3]:
        await message.answer(
            f"❌ Недостаточно товара.\n\n"
            f"В наличии: <b>{size[3]} шт.</b>"
        )
        return

    product = get_product(size[1])

    if not product:
        await state.clear()
        await message.answer("❌ Товар не найден.")
        return

    await state.update_data(
        quantity=quantity,
        product_id=product[0]
    )

    await state.set_state(Sale.actual_price)

    await message.answer(
        f"🛍 <b>{product[1]}</b>\n"
        f"Размер: <b>{size[2]}</b>\n"
        f"Количество: <b>{quantity} шт.</b>\n\n"
        f"Стандартная цена: <b>{money(product[3])}</b>\n\n"
        "Введите фактическую цену продажи за 1 шт. (₽):"
    )


@dp.message(Sale.actual_price)
async def sale_actual_price(
    message: Message,
    state: FSMContext
):

    try:
        actual_price = float(
            message.text.replace(",", ".")
        )

        if actual_price < 0:
            raise ValueError

    except ValueError:
        await message.answer(
            "❌ Введите корректную цену.\n"
            "Например: 2990"
        )
        return

    await state.update_data(
        actual_price=actual_price
    )

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="💵 Получена сразу",
                    callback_data="payment:cash"
                )
            ],
            [
                InlineKeyboardButton(
                    text="📦 Avito Доставка",
                    callback_data="payment:avito"
                )
            ]
        ]
    )

    await message.answer(
        "💳 <b>Способ оплаты</b>\n\n"
        "Как клиент оплачивает заказ?",
        reply_markup=keyboard
    )


@dp.callback_query(F.data.startswith("payment:"))
async def sale_payment(
    callback: CallbackQuery,
    state: FSMContext
):

    payment_method = callback.data.split(":")[1]
    data = await state.get_data()

    size_id = data["sale_size_id"]
    quantity = data["quantity"]
    actual_price = data["actual_price"]
    product_id = data["product_id"]

    size = get_size(size_id)
    product = get_product(product_id)

    if not size or not product:
        await state.clear()
        await callback.answer(
            "Ошибка: товар не найден.",
            show_alert=True
        )
        return

    if quantity > size[3]:
        await state.clear()
        await callback.answer(
            "Недостаточно товара.",
            show_alert=True
        )
        return

    purchase_price = product[2]

    total_sale = actual_price * quantity
    total_purchase = purchase_price * quantity
    total_profit = total_sale - total_purchase

    if payment_method == "cash":
        status = "completed"
    else:
        status = "pending"

    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        UPDATE product_sizes
        SET stock = stock - ?
        WHERE id = ?
    """, (
        quantity,
        size_id
    ))

    # ВАЖНО:
    # Теперь сохраняем size_id.
    # Именно это исправляет проблему возвратов.
    cur.execute("""
        INSERT INTO sales (
            product_id,
            size_id,
            product_name,
            size,
            quantity,
            sale_price,
            purchase_price,
            profit,
            payment_method,
            status
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        product_id,
        size_id,
        product[1],
        size[2],
        quantity,
        actual_price,
        purchase_price,
        total_profit,
        payment_method,
        status
    ))

    conn.commit()
    conn.close()

    await state.clear()

    if payment_method == "cash":

        await callback.message.edit_text(
            "✅ <b>Продажа оформлена!</b>\n\n"
            f"🛍 {product[1]}\n"
            f"Размер: {size[2]}\n"
            f"Количество: {quantity} шт.\n"
            f"Цена: {money(actual_price)} / шт.\n"
            f"Выручка: {money(total_sale)}\n"
            f"Прибыль: {money(total_profit)}\n\n"
            "💵 Оплата получена."
        )

    else:

        await callback.message.edit_text(
            "📦 <b>Заказ оформлен через Avito</b>\n\n"
            f"🛍 {product[1]}\n"
            f"Размер: {size[2]}\n"
            f"Количество: {quantity} шт.\n"
            f"Цена: {money(actual_price)} / шт.\n"
            f"Выручка: {money(total_sale)}\n"
            f"Потенциальная прибыль: {money(total_profit)}\n\n"
            "⏳ Деньги пока не получены.\n"
            "Заказ добавлен в ожидающие."
        )

    await callback.answer()


# =========================================================
# СТАТИСТИКА
# =========================================================

async def show_statistics(message: Message):

    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        SELECT
            COUNT(*),
            COALESCE(SUM(quantity), 0),
            COALESCE(SUM(profit), 0)
        FROM sales
        WHERE status = 'completed'
    """)

    completed_count, completed_quantity, completed_profit = cur.fetchone()

    cur.execute("""
        SELECT
            COUNT(*),
            COALESCE(SUM(quantity), 0)
        FROM sales
        WHERE status = 'pending'
    """)

    pending_count, pending_quantity = cur.fetchone()

    cur.execute("""
        SELECT
            COUNT(*),
            COALESCE(SUM(quantity), 0)
        FROM sales
        WHERE status = 'returned'
    """)

    returned_count, returned_quantity = cur.fetchone()

    conn.close()

    text = (
        "📊 <b>СТАТИСТИКА</b>\n\n"
        f"✅ Завершённых продаж: <b>{completed_count}</b>\n"
        f"📦 Продано: <b>{completed_quantity} шт.</b>\n"
        f"💰 Прибыль: <b>{money(completed_profit)}</b>\n\n"
        f"⏳ Ожидающих заказов: <b>{pending_count}</b>\n"
        f"📦 Товаров в ожидании: <b>{pending_quantity} шт.</b>\n\n"
        f"↩️ Возвратов: <b>{returned_count}</b>\n"
        f"📦 Возвращено: <b>{returned_quantity} шт.</b>"
    )

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="↩️ Возвраты",
                    callback_data="returns_list"
                )
            ]
        ]
    )

    await message.answer(
        text,
        reply_markup=keyboard
    )


# =========================================================
# ФИНАНСЫ
# =========================================================

async def show_profit(message: Message):

    conn = get_db()
    cur = conn.cursor()

    # Уже получено
    cur.execute("""
        SELECT
            COALESCE(SUM(sale_price * quantity), 0),
            COALESCE(SUM(purchase_price * quantity), 0),
            COALESCE(SUM(profit), 0),
            COALESCE(SUM(quantity), 0),
            COUNT(*)
        FROM sales
        WHERE status = 'completed'
    """)
    received_revenue, received_cost, received_profit, received_qty, received_orders = cur.fetchone()

    # Ожидается
    cur.execute("""
        SELECT
            COALESCE(SUM(sale_price * quantity), 0),
            COALESCE(SUM(purchase_price * quantity), 0),
            COALESCE(SUM(profit), 0),
            COALESCE(SUM(quantity), 0),
            COUNT(*)
        FROM sales
        WHERE status = 'pending'
    """)
    pending_revenue, pending_cost, pending_profit, pending_qty, pending_orders = cur.fetchone()

    # Возвраты
    cur.execute("""
        SELECT
            COALESCE(SUM(sale_price * quantity), 0),
            COALESCE(SUM(purchase_price * quantity), 0),
            COALESCE(SUM(profit), 0),
            COALESCE(SUM(quantity), 0),
            COUNT(*)
        FROM sales
        WHERE status = 'returned'
    """)
    returned_revenue, returned_cost, returned_profit, returned_qty, returned_orders = cur.fetchone()

    cur.execute("SELECT COALESCE(SUM(amount), 0) FROM expenses")
    expenses = cur.fetchone()[0]

    conn.close()

    # -----------------------------------------------------
    # РУЧНЫЕ ПЕРЕОПРЕДЕЛЕНИЯ
    # -----------------------------------------------------
    def ov(key, automatic):
        value = get_profit_override(key)
        return automatic if value is None else value

    received_revenue = ov("received_revenue", received_revenue)
    received_cost = ov("received_cost", received_cost)
    expenses = ov("expenses", expenses)
    net_received_profit = ov("net_profit", received_profit - expenses)
    received_orders = int(ov("received_orders", received_orders))
    received_qty = int(ov("received_qty", received_qty))

    pending_revenue = ov("pending_revenue", pending_revenue)
    pending_profit = ov("pending_profit", pending_profit)
    pending_orders = int(ov("pending_orders", pending_orders))
    pending_qty = int(ov("pending_qty", pending_qty))

    total_revenue_after_pending = ov(
        "total_revenue_after_pending",
        received_revenue + pending_revenue
    )
    net_after_pending = ov(
        "net_after_pending",
        net_received_profit + pending_profit
    )

    returned_orders = int(ov("returned_orders", returned_orders))
    returned_qty = int(ov("returned_qty", returned_qty))
    returned_revenue = ov("returned_revenue", returned_revenue)

    text = (
        "💰 <b>ПРИБЫЛЬ И ДЕНЬГИ</b>\n\n"
        "✅ <b>УЖЕ ПОЛУЧЕНО</b>\n"
        f"💵 Выручка: <b>{money(received_revenue)}</b>\n"
        f"📦 Себестоимость: <b>{money(received_cost)}</b>\n"
        f"💸 Расходы: <b>{money(expenses)}</b>\n"
        f"📈 <b>Чистая прибыль: {money(net_received_profit)}</b>\n"
        f"🧾 Продаж: <b>{received_orders}</b> | Товаров: <b>{received_qty} шт.</b>\n\n"
        "⏳ <b>ОЖИДАЕТСЯ ПОЛУЧИТЬ</b>\n"
        f"💵 Выручка: <b>{money(pending_revenue)}</b>\n"
        f"📈 Прибыль: <b>{money(pending_profit)}</b>\n"
        f"🧾 Заказов: <b>{pending_orders}</b> | Товаров: <b>{pending_qty} шт.</b>\n\n"
        "📊 <b>ЕСЛИ ВСЕ ОЖИДАЮЩИЕ ЗАКАЗЫ БУДУТ ПОЛУЧЕНЫ</b>\n"
        f"💵 Общая выручка: <b>{money(total_revenue_after_pending)}</b>\n"
        f"📈 Ожидаемая чистая прибыль: <b>{money(net_after_pending)}</b>\n\n"
        "↩️ <b>ВОЗВРАТЫ</b>\n"
        f"Заказов: <b>{returned_orders}</b> | Товаров: <b>{returned_qty} шт.</b>\n"
        f"Сумма продаж: <b>{money(returned_revenue)}</b>"
    )

    keyboard_rows = [
        [InlineKeyboardButton(text="➕ Добавить расход", callback_data="expense_add")],
        [InlineKeyboardButton(text="📋 История расходов", callback_data="expense_history")],
        [InlineKeyboardButton(text="⏳ Ожидающие заказы", callback_data="pending_orders")],
        [InlineKeyboardButton(text="↩️ Возвраты", callback_data="returns_list")],
    ]

    if message.from_user.id == ADMIN_ID:
        keyboard_rows.append([
            InlineKeyboardButton(
                text="⚙️ Настройки прибыли",
                callback_data="profit_settings"
            )
        ])

    await message.answer(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard_rows)
    )


# =========================================================
# НАСТРОЙКИ ПОКАЗАТЕЛЕЙ ПРИБЫЛИ — ТОЛЬКО СОЗДАТЕЛЬ
# =========================================================

PROFIT_SETTING_LABELS = {
    "received_revenue": ("💰 Выручка", False),
    "received_cost": ("📦 Себестоимость", False),
    "expenses": ("💸 Расходы", False),
    "net_profit": ("📈 Чистая прибыль", False),
    "received_orders": ("🧾 Продаж", True),
    "received_qty": ("📦 Товаров", True),
    "pending_revenue": ("⏳ Ожидаемая выручка", False),
    "pending_profit": ("⏳ Ожидаемая прибыль", False),
    "pending_orders": ("⏳ Ожидающих заказов", True),
    "pending_qty": ("⏳ Ожидающих товаров", True),
    "total_revenue_after_pending": ("📊 Общая выручка", False),
    "net_after_pending": ("📊 Ожидаемая чистая прибыль", False),
    "returned_orders": ("↩️ Возвратов", True),
    "returned_qty": ("↩️ Возвращено товаров", True),
    "returned_revenue": ("↩️ Сумма возвратов", False),
}


def get_profit_override(key: str):
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "SELECT value FROM profit_overrides WHERE key = ?",
        (key,)
    )
    row = cur.fetchone()
    conn.close()
    return None if row is None else float(row[0])


def set_profit_override(key: str, value: float):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO profit_overrides (key, value)
        VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
    """, (key, value))
    conn.commit()
    conn.close()


def clear_profit_override(key: str):
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "DELETE FROM profit_overrides WHERE key = ?",
        (key,)
    )
    conn.commit()
    conn.close()


def clear_all_profit_overrides():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("DELETE FROM profit_overrides")
    conn.commit()
    conn.close()


def profit_setting_display(key: str) -> str:
    value = get_profit_override(key)
    label, integer = PROFIT_SETTING_LABELS[key]
    if value is None:
        return f"{label}: <b>авто</b>"
    if integer:
        return f"{label}: <b>{int(value)}</b>"
    return f"{label}: <b>{money(value)}</b>"


async def show_profit_settings(callback: CallbackQuery):
    if not creator_guard(callback):
        await callback.answer("⛔ Только для создателя.", show_alert=True)
        return

    # Две колонки: изменение и обнуление. Отдельно внизу
    # есть возврат к автоматическому расчёту.
    rows = []
    for key, (label, integer) in PROFIT_SETTING_LABELS.items():
        rows.append([
            InlineKeyboardButton(
                text=f"✏️ {label}",
                callback_data=f"profit_edit:{key}"
            ),
            InlineKeyboardButton(
                text="🗑️ 0",
                callback_data=f"profit_zero:{key}"
            )
        ])

    rows.extend([
        [
            InlineKeyboardButton(
                text="🔄 Всё автоматически",
                callback_data="profit_auto_all"
            )
        ],
        [
            InlineKeyboardButton(
                text="⬅️ Назад к прибыли",
                callback_data="back_to_profit"
            )
        ],
    ])

    text = (
        "⚙️ <b>НАСТРОЙКИ ПРИБЫЛИ</b>\n\n"
        "Здесь ты можешь вручную менять ЛЮБОЙ показатель, "
        "который отображается в разделе прибыли.\n\n"
        "✏️ — ввести своё значение\n"
        "🗑️ 0 — обнулить показатель\n"
        "🔄 Всё автоматически — убрать все ручные значения\n\n"
        "Текущие ручные значения:\n"
    )

    for key in PROFIT_SETTING_LABELS:
        text += profit_setting_display(key) + "\n"

    await callback.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows)
    )
    await callback.answer()


@dp.callback_query(F.data == "profit_settings")
async def profit_settings(callback: CallbackQuery):
    await show_profit_settings(callback)


@dp.callback_query(F.data.startswith("profit_edit:"))
async def profit_edit_start(
    callback: CallbackQuery,
    state: FSMContext
):
    if not creator_guard(callback):
        await callback.answer("⛔ Только для создателя.", show_alert=True)
        return

    key = callback.data.split(":", 1)[1]
    if key not in PROFIT_SETTING_LABELS:
        await callback.answer("Неизвестный показатель.", show_alert=True)
        return

    label, integer = PROFIT_SETTING_LABELS[key]
    current = get_profit_override(key)
    current_text = "авто" if current is None else (str(int(current)) if integer else money(current))

    await state.clear()
    await state.update_data(
        profit_key=key,
        profit_integer=integer
    )
    await state.set_state(ProfitSettingsEdit.value)

    await callback.message.edit_text(
        "✏️ <b>ИЗМЕНЕНИЕ ПОКАЗАТЕЛЯ</b>\n\n"
        f"Показатель: <b>{label}</b>\n"
        f"Сейчас: <b>{current_text}</b>\n\n"
        "Введите новое значение.\n"
        "Например: <code>12262</code>\n\n"
        "Чтобы вернуться без изменения, нажмите «Отмена»." ,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отмена", callback_data="profit_edit_cancel")]
        ])
    )
    await callback.answer()


@dp.message(ProfitSettingsEdit.value)
async def profit_edit_value(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        await state.clear()
        return

    data = await state.get_data()
    key = data.get("profit_key")
    integer = data.get("profit_integer", False)

    if not key or key not in PROFIT_SETTING_LABELS:
        await state.clear()
        await message.answer("❌ Настройка потеряна. Откройте прибыль заново.")
        return

    try:
        raw = message.text.replace(" ", "").replace(",", ".")
        value = float(raw)
        if value < 0:
            raise ValueError
        if integer and not value.is_integer():
            raise ValueError
    except (ValueError, AttributeError):
        await message.answer(
            "❌ Некорректное значение.\n\n"
            "Для денег: <code>12262</code>\n"
            "Для количества: <code>3</code>"
        )
        return

    set_profit_override(key, value)
    await state.clear()

    label, _ = PROFIT_SETTING_LABELS[key]
    display = str(int(value)) if integer else money(value)

    await message.answer(
        "✅ <b>Показатель изменён</b>\n\n"
        f"{label}: <b>{display}</b>"
    )

    # После сохранения сразу показываем панель настроек новым сообщением.
    await send_profit_settings_message(message)


async def send_profit_settings_message(message: Message):
    rows = []
    for key, (label, integer) in PROFIT_SETTING_LABELS.items():
        rows.append([
            InlineKeyboardButton(
                text=f"✏️ {label}",
                callback_data=f"profit_edit:{key}"
            ),
            InlineKeyboardButton(
                text="🗑️ 0",
                callback_data=f"profit_zero:{key}"
            )
        ])

    rows.extend([
        [InlineKeyboardButton(text="🔄 Всё автоматически", callback_data="profit_auto_all")],
        [InlineKeyboardButton(text="⬅️ Назад к прибыли", callback_data="back_to_profit")],
    ])

    text = (
        "⚙️ <b>НАСТРОЙКИ ПРИБЫЛИ</b>\n\n"
        "Здесь можно вручную менять любой показатель прибыли.\n\n"
    )
    for key in PROFIT_SETTING_LABELS:
        text += profit_setting_display(key) + "\n"

    await message.answer(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows)
    )


@dp.callback_query(F.data.startswith("profit_zero:"))
async def profit_zero(callback: CallbackQuery):
    if not creator_guard(callback):
        await callback.answer("⛔ Только для создателя.", show_alert=True)
        return

    key = callback.data.split(":", 1)[1]
    if key not in PROFIT_SETTING_LABELS:
        await callback.answer("Неизвестный показатель.", show_alert=True)
        return

    set_profit_override(key, 0)
    await callback.answer("✅ Показатель обнулён.")
    await show_profit_settings(callback)


@dp.callback_query(F.data == "profit_auto_all")
async def profit_auto_all(callback: CallbackQuery, state: FSMContext):
    if not creator_guard(callback):
        await callback.answer("⛔ Только для создателя.", show_alert=True)
        return

    await state.clear()
    clear_all_profit_overrides()
    await callback.answer("✅ Все показатели снова считаются автоматически.")
    await show_profit_settings(callback)


@dp.callback_query(F.data == "profit_edit_cancel")
async def profit_edit_cancel(callback: CallbackQuery, state: FSMContext):
    if not creator_guard(callback):
        await callback.answer("⛔ Только для создателя.", show_alert=True)
        return

    await state.clear()
    await show_profit_settings(callback)


@dp.callback_query(F.data == "open_profit")
async def open_profit(callback: CallbackQuery):
    if not creator_guard(callback):
        await callback.answer("⛔ Только для создателя.", show_alert=True)
        return
    await show_profit(callback.message)
    await callback.answer()


# =========================================================
# ПАНЕЛЬ СОЗДАТЕЛЯ
# =========================================================

async def settings_start(message: Message):
    """Главная панель создателя. Доступ только ADMIN_ID."""
    if message.from_user.id != ADMIN_ID:
        await message.answer("⛔ Настройки доступны только создателю.")
        return

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📦 Товары", callback_data="creator_products")],
            [InlineKeyboardButton(text="📊 Продажи и история", callback_data="creator_sales")],
            [InlineKeyboardButton(text="💰 Прибыль и расходы", callback_data="creator_profit")],
            [InlineKeyboardButton(text="📥 Остатки", callback_data="creator_stock")],
            [InlineKeyboardButton(text="🔐 Доступ сотрудников", callback_data="creator_access")],
            [InlineKeyboardButton(text="🗑️ Удаление данных", callback_data="creator_delete")],
            [InlineKeyboardButton(text="ℹ️ Что можно менять", callback_data="creator_info")],
        ]
    )

    await message.answer(
        "⚙️ <b>ПАНЕЛЬ СОЗДАТЕЛЯ</b>\n\n"
        "Это закрытый раздел только для тебя.\n\n"
        "Здесь можно управлять основными данными бота: товарами, ценами, остатками, продажами, "
        "расходами, прибылью и доступом сотрудников.\n\n"
        "⚠️ Изменения в истории продаж и удаление данных могут повлиять на статистику и прибыль.",
        reply_markup=keyboard
    )


def creator_main_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📦 Товары", callback_data="creator_products")],
            [InlineKeyboardButton(text="📊 Продажи и история", callback_data="creator_sales")],
            [InlineKeyboardButton(text="💰 Прибыль и расходы", callback_data="creator_profit")],
            [InlineKeyboardButton(text="📥 Остатки", callback_data="creator_stock")],
            [InlineKeyboardButton(text="🔐 Доступ сотрудников", callback_data="creator_access")],
            [InlineKeyboardButton(text="🗑️ Удаление данных", callback_data="creator_delete")],
            [InlineKeyboardButton(text="ℹ️ Что можно менять", callback_data="creator_info")],
        ]
    )


def creator_guard(callback: CallbackQuery) -> bool:
    return callback.from_user.id == ADMIN_ID


@dp.callback_query(F.data == "creator_products")
async def creator_products(callback: CallbackQuery):
    if not creator_guard(callback):
        await callback.answer("⛔ Только для создателя.", show_alert=True)
        return

    await callback.message.edit_text(
        "📦 <b>УПРАВЛЕНИЕ ТОВАРАМИ</b>\n\n"
        "Здесь можно менять данные товаров и управлять их состоянием.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✏️ Изменить товар", callback_data="creator_edit_product")],
            [InlineKeyboardButton(text="➕ Добавить товар", callback_data="creator_add_product")],
            [InlineKeyboardButton(text="🗑️ Удалить товар", callback_data="creator_delete_product")],
            [InlineKeyboardButton(text="📥 Изменить остаток", callback_data="creator_stock")],
            [InlineKeyboardButton(text="⬅️ В панель создателя", callback_data="creator_home")],
        ])
    )
    await callback.answer()


@dp.callback_query(F.data == "creator_edit_product")
async def creator_edit_product(callback: CallbackQuery, state: FSMContext):
    if not creator_guard(callback):
        await callback.answer("⛔ Только для создателя.", show_alert=True)
        return
    await state.clear()
    await edit_product_start(callback.message)
    await callback.answer()


@dp.callback_query(F.data == "creator_add_product")
async def creator_add_product(callback: CallbackQuery, state: FSMContext):
    if not creator_guard(callback):
        await callback.answer("⛔ Только для создателя.", show_alert=True)
        return
    await state.clear()
    await add_product_start(callback.message, state)
    await callback.answer()


@dp.callback_query(F.data == "creator_delete_product")
async def creator_delete_product(callback: CallbackQuery):
    if not creator_guard(callback):
        await callback.answer("⛔ Только для создателя.", show_alert=True)
        return
    await delete_product_start(callback.message)
    await callback.answer()


@dp.callback_query(F.data == "creator_stock")
async def creator_stock(callback: CallbackQuery):
    if not creator_guard(callback):
        await callback.answer("⛔ Только для создателя.", show_alert=True)
        return
    await replenish_start(callback.message)
    await callback.answer()


@dp.callback_query(F.data == "creator_profit")
async def creator_profit(callback: CallbackQuery):
    if not creator_guard(callback):
        await callback.answer("⛔ Только для создателя.", show_alert=True)
        return

    await callback.message.edit_text(
        "💰 <b>УПРАВЛЕНИЕ ПРИБЫЛЬЮ</b>\n\n"
        "Здесь можно управлять финансовой частью бота.\n"
        "Кнопка «Настройки показателей» позволяет вручную "
        "изменять или обнулять значения прямо на экране прибыли.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⚙️ Настройки показателей", callback_data="profit_settings")],
            [InlineKeyboardButton(text="📊 Открыть прибыль", callback_data="open_profit")],
            [InlineKeyboardButton(text="💸 Добавить расход", callback_data="expense_add")],
            [InlineKeyboardButton(text="📋 История расходов", callback_data="expense_history")],
            [InlineKeyboardButton(text="📊 Редактор продаж", callback_data="creator_sales")],
            [InlineKeyboardButton(text="⬅️ В панель создателя", callback_data="creator_home")],
        ])
    )
    await callback.answer()


@dp.callback_query(F.data == "creator_sales")
async def creator_sales(callback: CallbackQuery):
    if not creator_guard(callback):
        await callback.answer("⛔ Только для создателя.", show_alert=True)
        return

    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, product_name, size, quantity, sale_price, purchase_price,
               profit, payment_method, status, created_at
        FROM sales
        ORDER BY id DESC
        LIMIT 30
    """)
    rows = cur.fetchall()
    conn.close()

    if not rows:
        await callback.message.edit_text(
            "📊 <b>РЕДАКТОР ПРОДАЖ</b>\n\nПродаж пока нет.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Назад", callback_data="creator_home")]
            ])
        )
        await callback.answer()
        return

    text = "📊 <b>РЕДАКТОР ПРОДАЖ</b>\n\n"
    buttons = []

    for sale_id, product_name, size, quantity, sale_price, purchase_price, profit, payment_method, status, created_at in rows:
        status_text = {
            "completed": "✅ получено",
            "pending": "⏳ ожидается",
            "returned": "↩️ возврат",
        }.get(status, status)

        text += (
            f"🆔 <b>#{sale_id}</b> — {product_name} / {size}\n"
            f"{quantity} шт. × {money(sale_price)} = {money(sale_price * quantity)}\n"
            f"Статус: {status_text} | Прибыль: {money(profit)}\n"
            f"🕒 {created_at}\n\n"
        )
        buttons.append([
            InlineKeyboardButton(
                text=f"✏️ Продажа #{sale_id}",
                callback_data=f"creator_sale_edit:{sale_id}"
            )
        ])

    buttons.append([InlineKeyboardButton(text="⬅️ В панель создателя", callback_data="creator_home")])

    await callback.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
    )
    await callback.answer()


@dp.callback_query(F.data == "creator_access")
async def creator_access(callback: CallbackQuery, state: FSMContext):
    if not creator_guard(callback):
        await callback.answer("⛔ Только для создателя.", show_alert=True)
        return
    await state.clear()
    await access_start(callback.message, state)
    await callback.answer()


@dp.callback_query(F.data == "creator_delete")
async def creator_delete(callback: CallbackQuery):
    if not creator_guard(callback):
        await callback.answer("⛔ Только для создателя.", show_alert=True)
        return

    await callback.message.edit_text(
        "🗑️ <b>УДАЛЕНИЕ ДАННЫХ</b>\n\n"
        "Удаление товара не удаляет историю его продаж.\n\n"
        "Удалять отдельные продажи автоматически не предлагаю, чтобы случайно не испортить расчёт прибыли. "
        "Для этого используется редактор продаж и возвраты.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🗑️ Удалить товар", callback_data="creator_delete_product")],
            [InlineKeyboardButton(text="⬅️ В панель создателя", callback_data="creator_home")],
        ])
    )
    await callback.answer()


@dp.callback_query(F.data == "creator_info")
async def creator_info(callback: CallbackQuery):
    if not creator_guard(callback):
        await callback.answer("⛔ Только для создателя.", show_alert=True)
        return

    await callback.message.edit_text(
        "ℹ️ <b>ЧТО МОЖНО МЕНЯТЬ</b>\n\n"
        "📦 Товары — название, закупочная цена, цена продажи.\n"
        "📥 Остатки — количество товара по размерам.\n"
        "📊 Продажи — контроль и корректировка данных продажи.\n"
        "💸 Расходы — добавление и редактирование расходов.\n"
        "💰 Прибыль — пересчитывается автоматически после изменения данных.\n"
        "🔐 Доступ — добавление и удаление сотрудников.\n\n"
        "Обычные сотрудники эту панель не видят.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ В панель создателя", callback_data="creator_home")]
        ])
    )
    await callback.answer()


@dp.callback_query(F.data == "creator_home")
async def creator_home(callback: CallbackQuery):
    if not creator_guard(callback):
        await callback.answer("⛔ Только для создателя.", show_alert=True)
        return

    await callback.message.edit_text(
        "⚙️ <b>ПАНЕЛЬ СОЗДАТЕЛЯ</b>\n\n"
        "Выбери раздел:",
        reply_markup=creator_main_keyboard()
    )
    await callback.answer()


@dp.callback_query(F.data == "settings_back")
async def settings_back(callback: CallbackQuery):
    if not creator_guard(callback):
        await callback.answer("⛔ Только для создателя.", show_alert=True)
        return
    await creator_home(callback)

# =========================================================
# РЕДАКТОР ПРОДАЖ — ТОЛЬКО СОЗДАТЕЛЬ
# =========================================================

@dp.callback_query(F.data.startswith("creator_sale_edit:"))
async def creator_sale_edit_start(callback: CallbackQuery, state: FSMContext):
    if not creator_guard(callback):
        await callback.answer("⛔ Только для создателя.", show_alert=True)
        return

    sale_id = int(callback.data.split(":")[1])

    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT product_name, size, quantity, sale_price, purchase_price,
               profit, status
        FROM sales
        WHERE id = ?
    """, (sale_id,))
    row = cur.fetchone()
    conn.close()

    if not row:
        await callback.answer("Продажа не найдена.", show_alert=True)
        return

    await state.clear()
    await state.update_data(edit_sale_id=sale_id)
    await state.set_state(EditSale.sale_price)

    product_name, size, quantity, sale_price, purchase_price, profit, status = row

    await callback.message.edit_text(
        f"✏️ <b>РЕДАКТИРОВАНИЕ ПРОДАЖИ #{sale_id}</b>\n\n"
        f"🛍 {product_name}\n"
        f"Размер: {size}\n"
        f"Количество: {quantity} шт.\n"
        f"Статус: {status}\n\n"
        f"Текущая цена продажи: <b>{money(sale_price)}</b>\n"
        f"Текущая закупка: <b>{money(purchase_price)}</b>\n"
        f"Текущая прибыль: <b>{money(profit)}</b>\n\n"
        "Введите новую цену продажи за 1 шт. (₽):"
    )
    await callback.answer()


@dp.message(EditSale.sale_price)
async def creator_sale_edit_sale_price(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        await state.clear()
        return

    try:
        sale_price = float(message.text.replace(",", "."))
        if sale_price < 0:
            raise ValueError
    except ValueError:
        await message.answer("❌ Введите корректную цену, например: 2990")
        return

    await state.update_data(new_sale_price=sale_price)
    await state.set_state(EditSale.purchase_price)
    await message.answer("Введите новую закупочную цену за 1 шт. (₽):")


@dp.message(EditSale.purchase_price)
async def creator_sale_edit_purchase_price(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        await state.clear()
        return

    try:
        purchase_price = float(message.text.replace(",", "."))
        if purchase_price < 0:
            raise ValueError
    except ValueError:
        await message.answer("❌ Введите корректную закупочную цену, например: 1500")
        return

    data = await state.get_data()
    sale_id = data["edit_sale_id"]
    sale_price = data["new_sale_price"]

    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT quantity, product_name, size, status FROM sales WHERE id = ?", (sale_id,))
    sale = cur.fetchone()

    if not sale:
        conn.close()
        await state.clear()
        await message.answer("❌ Продажа не найдена.", reply_markup=get_menu(message.from_user.id))
        return

    quantity, product_name, size, status = sale
    profit = (sale_price - purchase_price) * quantity

    cur.execute("""
        UPDATE sales
        SET sale_price = ?, purchase_price = ?, profit = ?
        WHERE id = ?
    """, (sale_price, purchase_price, profit, sale_id))
    conn.commit()
    conn.close()

    await state.clear()

    await message.answer(
        "✅ <b>Продажа исправлена</b>\n\n"
        f"🆔 Продажа #{sale_id}\n"
        f"🛍 {product_name}\n"
        f"Размер: {size}\n"
        f"Количество: {quantity} шт.\n"
        f"Цена продажи: {money(sale_price)} / шт.\n"
        f"Закупка: {money(purchase_price)} / шт.\n"
        f"Прибыль по продаже: <b>{money(profit)}</b>\n\n"
        "💰 Общая прибыль будет пересчитана автоматически.",
        reply_markup=get_menu(message.from_user.id)
    )


# =========================================================
# РАСХОДЫ
# =========================================================

@dp.callback_query(F.data == "expense_add")
async def add_expense_start(
    callback: CallbackQuery,
    state: FSMContext
):

    await state.clear()
    await state.set_state(Expense.description)

    await callback.message.edit_text(
        "💸 <b>Добавление расхода</b>\n\n"
        "Напишите, на что был расход.\n\n"
        "Например:\n"
        "Доставка\n"
        "Упаковка\n"
        "Такси"
    )

    await callback.answer()


@dp.message(Expense.description)
async def expense_description(
    message: Message,
    state: FSMContext
):

    description = message.text.strip()

    if not description:
        await message.answer(
            "❌ Напишите описание расхода."
        )
        return

    await state.update_data(
        description=description
    )

    await state.set_state(Expense.amount)

    await message.answer(
        "💰 Теперь введите сумму расхода (₽):\n\n"
        "Например: <code>1500</code>"
    )


@dp.message(Expense.amount)
async def expense_amount(
    message: Message,
    state: FSMContext
):

    try:
        amount = float(
            message.text.replace(",", ".")
        )

        if amount <= 0:
            raise ValueError

    except ValueError:
        await message.answer(
            "❌ Введите корректную положительную сумму.\n"
            "Например: 1500"
        )
        return

    data = await state.get_data()

    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO expenses (
            description,
            amount
        )
        VALUES (?, ?)
    """, (
        data["description"],
        amount
    ))

    conn.commit()
    conn.close()

    await state.clear()

    await message.answer(
        "✅ <b>Расход добавлен!</b>\n\n"
        f"📝 {data['description']}\n"
        f"💸 Сумма: <b>{money(amount)}</b>",
        reply_markup=get_menu(message.from_user.id)
    )


# =========================================================
# ИСТОРИЯ РАСХОДОВ
# =========================================================

@dp.callback_query(F.data == "expense_history")
async def expense_history(
    callback: CallbackQuery
):

    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        SELECT
            id,
            description,
            amount,
            created_at
        FROM expenses
        ORDER BY id DESC
        LIMIT 20
    """)

    rows = cur.fetchall()

    cur.execute("""
        SELECT COALESCE(SUM(amount), 0)
        FROM expenses
    """)

    total = cur.fetchone()[0]

    conn.close()

    if not rows:

        await callback.message.edit_text(
            "📋 <b>История расходов</b>\n\n"
            "Расходов пока нет.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="⬅️ Назад",
                            callback_data="back_to_profit"
                        )
                    ]
                ]
            )
        )

        await callback.answer()
        return

    text = "📋 <b>ИСТОРИЯ РАСХОДОВ</b>\n\n"

    for expense_id, description, amount, created_at in rows:
        text += (
            f"#{expense_id} — <b>{description}</b>\n"
            f"💸 {money(amount)}\n"
            f"🕒 {created_at}\n\n"
        )

    text += (
        "━━━━━━━━━━━━━━━━\n"
        f"💸 <b>Всего расходов: {money(total)}</b>"
    )

    await callback.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="⬅️ Назад",
                        callback_data="back_to_profit"
                    )
                ]
            ]
        )
    )

    await callback.answer()


# =========================================================
# ОЖИДАЮЩИЕ ЗАКАЗЫ
# =========================================================

@dp.callback_query(F.data == "pending_orders")
async def pending_orders(
    callback: CallbackQuery
):

    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        SELECT
            id,
            product_name,
            size,
            quantity,
            sale_price,
            profit,
            created_at
        FROM sales
        WHERE status = 'pending'
        ORDER BY id DESC
    """)

    rows = cur.fetchall()
    conn.close()

    if not rows:

        await callback.message.edit_text(
            "⏳ <b>ОЖИДАЮЩИЕ ЗАКАЗЫ</b>\n\n"
            "Нет заказов, ожидающих получения.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="⬅️ Назад",
                            callback_data="back_to_profit"
                        )
                    ]
                ]
            )
        )

        await callback.answer()
        return

    text = "⏳ <b>ОЖИДАЮЩИЕ ЗАКАЗЫ</b>\n\n"
    buttons = []

    for (
        sale_id,
        product_name,
        size,
        quantity,
        sale_price,
        profit,
        created_at
    ) in rows:

        total = sale_price * quantity

        text += (
            f"🆔 Заказ #{sale_id}\n"
            f"🛍 <b>{product_name}</b>\n"
            f"Размер: {size}\n"
            f"Количество: {quantity} шт.\n"
            f"Выручка: {money(total)}\n"
            f"Прибыль: {money(profit)}\n"
            f"🕒 {created_at}\n\n"
        )

        buttons.append([
            InlineKeyboardButton(
                text=f"📦 Заказ #{sale_id}",
                callback_data=f"pending_view:{sale_id}"
            )
        ])

    buttons.append([
        InlineKeyboardButton(
            text="⬅️ Назад",
            callback_data="back_to_profit"
        )
    ])

    await callback.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=buttons
        )
    )

    await callback.answer()


# =========================================================
# ПРОСМОТР ЗАКАЗА
# =========================================================

@dp.callback_query(F.data.startswith("pending_view:"))
async def pending_view(
    callback: CallbackQuery
):

    sale_id = int(callback.data.split(":")[1])

    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        SELECT
            id,
            product_name,
            size,
            quantity,
            sale_price,
            purchase_price,
            profit,
            payment_method,
            status,
            created_at
        FROM sales
        WHERE id = ?
    """, (sale_id,))

    sale = cur.fetchone()
    conn.close()

    if not sale:
        await callback.answer(
            "Заказ не найден.",
            show_alert=True
        )
        return

    if sale[8] != "pending":
        await callback.answer(
            "Этот заказ уже обработан.",
            show_alert=True
        )
        return

    total_revenue = sale[4] * sale[3]

    text = (
        f"⏳ <b>ЗАКАЗ #{sale[0]}</b>\n\n"
        f"🛍 Товар: <b>{sale[1]}</b>\n"
        f"Размер: <b>{sale[2]}</b>\n"
        f"Количество: <b>{sale[3]} шт.</b>\n"
        f"Цена: <b>{money(sale[4])}</b> / шт.\n"
        f"Выручка: <b>{money(total_revenue)}</b>\n"
        f"Прибыль: <b>{money(sale[6])}</b>\n\n"
        "Что произошло с заказом?"
    )

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Клиент получил",
                    callback_data=f"complete_order:{sale_id}"
                )
            ],
            [
                InlineKeyboardButton(
                    text="❌ Возврат",
                    callback_data=f"return_confirm:{sale_id}"
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ Назад",
                    callback_data="pending_orders"
                )
            ]
        ]
    )

    await callback.message.edit_text(
        text,
        reply_markup=keyboard
    )

    await callback.answer()


# =========================================================
# ЗАКАЗ ПОЛУЧЕН
# =========================================================

@dp.callback_query(F.data.startswith("complete_order:"))
async def complete_order(
    callback: CallbackQuery
):

    sale_id = int(callback.data.split(":")[1])

    conn = get_db()
    cur = conn.cursor()

    cur.execute(
        "SELECT status FROM sales WHERE id = ?",
        (sale_id,)
    )

    row = cur.fetchone()

    if not row:
        conn.close()
        await callback.answer(
            "Заказ не найден.",
            show_alert=True
        )
        return

    if row[0] != "pending":
        conn.close()
        await callback.answer(
            "Этот заказ уже обработан.",
            show_alert=True
        )
        return

    cur.execute("""
        UPDATE sales
        SET status = 'completed'
        WHERE id = ?
    """, (sale_id,))

    conn.commit()
    conn.close()

    await callback.answer(
        "Заказ отмечен как полученный."
    )

    await pending_orders(callback)


# =========================================================
# ПОДТВЕРЖДЕНИЕ ВОЗВРАТА
# =========================================================

@dp.callback_query(F.data.startswith("return_confirm:"))
async def return_confirm(
    callback: CallbackQuery
):

    sale_id = int(callback.data.split(":")[1])

    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        SELECT
            id,
            product_name,
            size,
            quantity,
            status
        FROM sales
        WHERE id = ?
    """, (sale_id,))

    sale = cur.fetchone()
    conn.close()

    if not sale:
        await callback.answer(
            "Заказ не найден.",
            show_alert=True
        )
        return

    if sale[4] != "pending":
        await callback.answer(
            "Этот заказ уже обработан.",
            show_alert=True
        )
        return

    text = (
        "⚠️ <b>ПОДТВЕРЖДЕНИЕ ВОЗВРАТА</b>\n\n"
        "Вы уверены, что хотите оформить возврат?\n\n"
        f"🛍 Товар: <b>{sale[1]}</b>\n"
        f"Размер: <b>{sale[2]}</b>\n"
        f"Количество: <b>{sale[3]} шт.</b>\n\n"
        "Товар будет возвращён на склад."
    )

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="❌ Да, оформить возврат",
                    callback_data=f"return_execute:{sale_id}"
                )
            ],
            [
                InlineKeyboardButton(
                    text="↩️ Отмена",
                    callback_data=f"pending_view:{sale_id}"
                )
            ]
        ]
    )

    await callback.message.edit_text(
        text,
        reply_markup=keyboard
    )

    await callback.answer()


# =========================================================
# ОФОРМЛЕНИЕ ВОЗВРАТА
# =========================================================

@dp.callback_query(F.data.startswith("return_execute:"))
async def return_execute(
    callback: CallbackQuery
):

    sale_id = int(callback.data.split(":")[1])

    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        SELECT
            product_id,
            size_id,
            size,
            quantity,
            status
        FROM sales
        WHERE id = ?
    """, (sale_id,))

    sale = cur.fetchone()

    if not sale:
        conn.close()
        await callback.answer(
            "Заказ не найден.",
            show_alert=True
        )
        return

    product_id, size_id, size_name, quantity, status = sale

    if status != "pending":
        conn.close()
        await callback.answer(
            "Этот заказ уже обработан.",
            show_alert=True
        )
        return

    # Сначала используем точный size_id
    if size_id:

        cur.execute("""
            SELECT id
            FROM product_sizes
            WHERE id = ?
        """, (size_id,))

        size_row = cur.fetchone()

        if not size_row:
            conn.close()

            await callback.answer(
                "Размер товара был удалён. "
                "Нельзя автоматически вернуть товар на склад.",
                show_alert=True
            )
            return

        cur.execute("""
            UPDATE product_sizes
            SET stock = stock + ?
            WHERE id = ?
        """, (
            quantity,
            size_id
        ))

    else:

        # Запасной вариант для очень старой продажи
        cur.execute("""
            SELECT id
            FROM product_sizes
            WHERE product_id = ?
              AND LOWER(TRIM(size)) = LOWER(TRIM(?))
            LIMIT 1
        """, (
            product_id,
            size_name
        ))

        size_row = cur.fetchone()

        if not size_row:
            conn.close()

            await callback.answer(
                "Не удалось найти размер товара.",
                show_alert=True
            )
            return

        real_size_id = size_row[0]

        cur.execute("""
            UPDATE sales
            SET size_id = ?
            WHERE id = ?
        """, (
            real_size_id,
            sale_id
        ))

        cur.execute("""
            UPDATE product_sizes
            SET stock = stock + ?
            WHERE id = ?
        """, (
            quantity,
            real_size_id
        ))

    cur.execute("""
        UPDATE sales
        SET status = 'returned'
        WHERE id = ?
    """, (sale_id,))

    conn.commit()
    conn.close()

    await callback.answer("Возврат оформлен.")

    await pending_orders(callback)


# =========================================================
# СПИСОК ВОЗВРАТОВ
# =========================================================

@dp.callback_query(F.data == "returns_list")
async def returns_list(
    callback: CallbackQuery
):

    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        SELECT
            id,
            product_id,
            product_name,
            size,
            quantity,
            sale_price,
            created_at
        FROM sales
        WHERE status = 'returned'
        ORDER BY id DESC
    """)

    rows = cur.fetchall()
    conn.close()

    if not rows:

        await callback.message.edit_text(
            "↩️ <b>ВОЗВРАТЫ</b>\n\n"
            "Возвратов нет.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="⬅️ Назад",
                            callback_data="back_to_profit"
                        )
                    ]
                ]
            )
        )

        await callback.answer()
        return

    text = "↩️ <b>ВОЗВРАТЫ</b>\n\n"
    buttons = []

    for (
        sale_id,
        product_id,
        product_name,
        size,
        quantity,
        sale_price,
        created_at
    ) in rows:

        text += (
            f"🆔 Возврат #{sale_id}\n"
            f"🛍 <b>{product_name}</b>\n"
            f"Размер: {size}\n"
            f"Количество: {quantity} шт.\n"
            f"Сумма: {money(sale_price * quantity)}\n"
            f"🕒 {created_at}\n\n"
        )

        buttons.append([
            InlineKeyboardButton(
                text=f"↩️ Возврат #{sale_id}",
                callback_data=f"return_undo_confirm:{sale_id}"
            )
        ])

    buttons.append([
        InlineKeyboardButton(
            text="⬅️ Назад",
            callback_data="back_to_profit"
        )
    ])

    await callback.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=buttons
        )
    )

    await callback.answer()


# =========================================================
# ОТМЕНА ВОЗВРАТА
# =========================================================

@dp.callback_query(F.data.startswith("return_undo_confirm:"))
async def return_undo_confirm(
    callback: CallbackQuery
):

    sale_id = int(callback.data.split(":")[1])

    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        SELECT
            product_id,
            size_id,
            product_name,
            size,
            quantity,
            status
        FROM sales
        WHERE id = ?
    """, (sale_id,))

    sale = cur.fetchone()
    conn.close()

    if not sale:
        await callback.answer(
            "Продажа не найдена.",
            show_alert=True
        )
        return

    product_id, size_id, product_name, size_name, quantity, status = sale

    if status != "returned":
        await callback.answer(
            "Этот возврат уже отменён.",
            show_alert=True
        )
        return

    # -----------------------------------------------------
    # Если size_id уже есть — обычная отмена
    # -----------------------------------------------------

    if size_id:

        text = (
            "⚠️ <b>ОТМЕНА ВОЗВРАТА</b>\n\n"
            f"Товар: <b>{product_name}</b>\n"
            f"Размер: <b>{size_name}</b>\n"
            f"Количество: <b>{quantity} шт.</b>\n\n"
            "Товар будет снова списан со склада,\n"
            "а возврат исчезнет из статистики.\n\n"
            "Продолжить?"
        )

        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="✅ Да, отменить возврат",
                        callback_data=f"return_undo:{sale_id}"
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="↩️ Назад",
                        callback_data="returns_list"
                    )
                ]
            ]
        )

        await callback.message.edit_text(
            text,
            reply_markup=keyboard
        )

        await callback.answer()
        return

    # -----------------------------------------------------
    # СТАРАЯ ПРОДАЖА БЕЗ size_id
    #
    # Ищем размер по тексту.
    # -----------------------------------------------------

    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        SELECT id, size, stock
        FROM product_sizes
        WHERE product_id = ?
          AND LOWER(TRIM(size)) = LOWER(TRIM(?))
    """, (
        product_id,
        size_name
    ))

    exact = cur.fetchone()

    if exact:
        conn.close()

        text = (
            "⚠️ <b>ОТМЕНА ВОЗВРАТА</b>\n\n"
            f"Товар: <b>{product_name}</b>\n"
            f"Размер: <b>{exact[1]}</b>\n"
            f"Количество: <b>{quantity} шт.</b>\n\n"
            "Возврат будет убран из статистики,\n"
            "а товар снова будет считаться проданным.\n\n"
            "Продолжить?"
        )

        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="✅ Да, отменить возврат",
                        callback_data=f"return_undo:{sale_id}"
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="↩️ Назад",
                        callback_data="returns_list"
                    )
                ]
            ]
        )

        await callback.message.edit_text(
            text,
            reply_markup=keyboard
        )

        await callback.answer()
        return

    # -----------------------------------------------------
    # Если старый размер не найден:
    # предлагаем выбрать существующий размер вручную
    # -----------------------------------------------------

    cur.execute("""
        SELECT id, size, stock
        FROM product_sizes
        WHERE product_id = ?
        ORDER BY id
    """, (product_id,))

    sizes = cur.fetchall()
    conn.close()

    if not sizes:

        await callback.answer(
            "У товара вообще нет размеров. "
            "Добавьте размер товара и повторите.",
            show_alert=True
        )
        return

    text = (
        "⚠️ <b>СТАРЫЙ ВОЗВРАТ</b>\n\n"
        f"Товар: <b>{product_name}</b>\n"
        f"Старый размер: <b>{size_name}</b>\n"
        f"Количество: <b>{quantity} шт.</b>\n\n"
        "Старый размер не удалось автоматически найти.\n"
        "Выберите размер, с которого нужно списать "
        "товар при отмене возврата:"
    )

    buttons = []

    for current_size_id, current_size, stock in sizes:

        buttons.append([
            InlineKeyboardButton(
                text=f"{current_size} — {stock} шт.",
                callback_data=(
                    f"return_undo_size:"
                    f"{sale_id}:{current_size_id}"
                )
            )
        ])

    buttons.append([
        InlineKeyboardButton(
            text="↩️ Назад",
            callback_data="returns_list"
        )
    ])

    await callback.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=buttons
        )
    )

    await callback.answer()


# =========================================================
# ВЫБОР РАЗМЕРА ДЛЯ СТАРОГО ВОЗВРАТА
# =========================================================

@dp.callback_query(F.data.startswith("return_undo_size:"))
async def return_undo_size(
    callback: CallbackQuery
):

    parts = callback.data.split(":")

    sale_id = int(parts[1])
    size_id = int(parts[2])

    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        SELECT
            product_name,
            size,
            quantity,
            status
        FROM sales
        WHERE id = ?
    """, (sale_id,))

    sale = cur.fetchone()

    cur.execute("""
        SELECT
            size,
            stock
        FROM product_sizes
        WHERE id = ?
    """, (size_id,))

    size_row = cur.fetchone()

    conn.close()

    if not sale or not size_row:
        await callback.answer(
            "Данные не найдены.",
            show_alert=True
        )
        return

    if sale[3] != "returned":
        await callback.answer(
            "Этот возврат уже обработан.",
            show_alert=True
        )
        return

    product_name, old_size, quantity, _ = sale
    new_size, stock = size_row

    text = (
        "⚠️ <b>ПОДТВЕРЖДЕНИЕ</b>\n\n"
        f"Товар: <b>{product_name}</b>\n"
        f"Количество: <b>{quantity} шт.</b>\n\n"
        f"Старый размер: <b>{old_size}</b>\n"
        f"Выбранный размер: <b>{new_size}</b>\n"
        f"Сейчас на складе: <b>{stock} шт.</b>\n\n"
        "При отмене возврата это количество "
        "будет списано с выбранного размера.\n\n"
        "Продолжить?"
    )

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Да, отменить",
                    callback_data=(
                        f"return_undo_force:"
                        f"{sale_id}:{size_id}"
                    )
                )
            ],
            [
                InlineKeyboardButton(
                    text="↩️ Назад",
                    callback_data=f"return_undo_confirm:{sale_id}"
                )
            ]
        ]
    )

    await callback.message.edit_text(
        text,
        reply_markup=keyboard
    )

    await callback.answer()


# =========================================================
# ОТМЕНА ВОЗВРАТА
# =========================================================

@dp.callback_query(F.data.startswith("return_undo_force:"))
async def return_undo_force(
    callback: CallbackQuery
):

    parts = callback.data.split(":")

    sale_id = int(parts[1])
    size_id = int(parts[2])

    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        SELECT
            quantity,
            status
        FROM sales
        WHERE id = ?
    """, (sale_id,))

    sale = cur.fetchone()

    if not sale:
        conn.close()
        await callback.answer(
            "Продажа не найдена.",
            show_alert=True
        )
        return

    quantity, status = sale

    if status != "returned":
        conn.close()
        await callback.answer(
            "Этот возврат уже отменён.",
            show_alert=True
        )
        return

    cur.execute("""
        SELECT stock
        FROM product_sizes
        WHERE id = ?
    """, (size_id,))

    size_row = cur.fetchone()

    if not size_row:
        conn.close()
        await callback.answer(
            "Размер больше не существует.",
            show_alert=True
        )
        return

    current_stock = size_row[0]

    if current_stock < quantity:
        conn.close()
        await callback.answer(
            "Нельзя отменить возврат: "
            "на выбранном размере недостаточно товара.",
            show_alert=True
        )
        return

    cur.execute("""
        UPDATE product_sizes
        SET stock = stock - ?
        WHERE id = ?
    """, (
        quantity,
        size_id
    ))

    cur.execute("""
        UPDATE sales
        SET
            status = 'completed',
            size_id = ?
        WHERE id = ?
    """, (
        size_id,
        sale_id
    ))

    conn.commit()
    conn.close()

    await callback.answer(
        "Возврат отменён."
    )

    await returns_list(callback)


# Обычная отмена возврата для новых продаж
@dp.callback_query(F.data.startswith("return_undo:"))
async def return_undo(
    callback: CallbackQuery
):

    sale_id = int(callback.data.split(":")[1])

    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        SELECT
            size_id,
            quantity,
            status
        FROM sales
        WHERE id = ?
    """, (sale_id,))

    sale = cur.fetchone()

    if not sale:
        conn.close()
        await callback.answer(
            "Продажа не найдена.",
            show_alert=True
        )
        return

    size_id, quantity, status = sale

    if status != "returned":
        conn.close()
        await callback.answer(
            "Этот возврат уже отменён.",
            show_alert=True
        )
        return

    if not size_id:
        conn.close()

        await callback.answer(
            "Для старого возврата нужно выбрать размер.",
            show_alert=True
        )

        return

    cur.execute("""
        SELECT stock
        FROM product_sizes
        WHERE id = ?
    """, (size_id,))

    stock_row = cur.fetchone()

    if not stock_row:
        conn.close()

        await callback.answer(
            "Размер товара больше не существует.",
            show_alert=True
        )
        return

    current_stock = stock_row[0]

    if current_stock < quantity:
        conn.close()

        await callback.answer(
            "Нельзя отменить возврат: "
            "на складе недостаточно товара.",
            show_alert=True
        )
        return

    cur.execute("""
        UPDATE product_sizes
        SET stock = stock - ?
        WHERE id = ?
    """, (
        quantity,
        size_id
    ))

    cur.execute("""
        UPDATE sales
        SET status = 'completed'
        WHERE id = ?
    """, (sale_id,))

    conn.commit()
    conn.close()

    await callback.answer(
        "Возврат отменён."
    )

    await returns_list(callback)


# =========================================================
# НАЗАД В ФИНАНСЫ
# =========================================================

@dp.callback_query(F.data == "back_to_profit")
async def back_to_profit(
    callback: CallbackQuery
):

    try:
        await callback.message.delete()
    except Exception:
        pass

    await show_profit(callback.message)

    await callback.answer()


# =========================================================
# ИЗМЕНЕНИЕ ТОВАРА
# =========================================================

async def edit_product_start(message: Message):

    products = get_products()

    if not products:
        await message.answer("✏️ Товаров пока нет.")
        return

    buttons = []

    for product_id, name, _, _ in products:
        buttons.append([
            InlineKeyboardButton(
                text=name,
                callback_data=f"edit_product:{product_id}"
            )
        ])

    await message.answer(
        "✏️ <b>Изменение товара</b>\n\n"
        "Выберите товар:",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=buttons
        )
    )


@dp.callback_query(F.data.startswith("edit_product:"))
async def edit_product_select(
    callback: CallbackQuery,
    state: FSMContext
):

    product_id = int(callback.data.split(":")[1])
    product = get_product(product_id)

    if not product:
        await callback.answer(
            "Товар не найден.",
            show_alert=True
        )
        return

    await state.update_data(
        edit_product_id=product_id
    )

    await state.set_state(EditProduct.name)

    await callback.message.edit_text(
        f"✏️ <b>Изменение товара</b>\n\n"
        f"Текущее название: <b>{product[1]}</b>\n\n"
        "Введите новое название:"
    )

    await callback.answer()


@dp.message(EditProduct.name)
async def edit_product_name(
    message: Message,
    state: FSMContext
):

    name = message.text.strip()

    if not name:
        await message.answer("Введите название.")
        return

    await state.update_data(new_name=name)
    await state.set_state(EditProduct.purchase_price)

    await message.answer(
        "Введите новую закупочную цену (₽):"
    )


@dp.message(EditProduct.purchase_price)
async def edit_product_purchase(
    message: Message,
    state: FSMContext
):

    try:
        price = float(
            message.text.replace(",", ".")
        )

        if price < 0:
            raise ValueError

    except ValueError:
        await message.answer(
            "❌ Введите корректную цену."
        )
        return

    await state.update_data(
        new_purchase_price=price
    )

    await state.set_state(EditProduct.sale_price)

    await message.answer(
        "Введите новую цену продажи (₽):"
    )


@dp.message(EditProduct.sale_price)
async def edit_product_sale(
    message: Message,
    state: FSMContext
):

    try:
        price = float(
            message.text.replace(",", ".")
        )

        if price < 0:
            raise ValueError

    except ValueError:
        await message.answer(
            "❌ Введите корректную цену."
        )
        return

    data = await state.get_data()

    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        UPDATE products
        SET
            name = ?,
            purchase_price = ?,
            sale_price = ?
        WHERE id = ?
    """, (
        data["new_name"],
        data["new_purchase_price"],
        price,
        data["edit_product_id"]
    ))

    conn.commit()
    conn.close()

    await state.clear()

    await message.answer(
        "✅ <b>Товар изменён!</b>\n\n"
        f"🛍 {data['new_name']}\n"
        f"Закупка: {money(data['new_purchase_price'])}\n"
        f"Продажа: {money(price)}",
        reply_markup=get_menu(message.from_user.id)
    )


# =========================================================
# УДАЛЕНИЕ ТОВАРА
# =========================================================

async def delete_product_start(message: Message):

    products = get_products()

    if not products:
        await message.answer("🗑️ Товаров пока нет.")
        return

    buttons = []

    for product_id, name, _, _ in products:
        buttons.append([
            InlineKeyboardButton(
                text=f"🗑️ {name}",
                callback_data=f"delete_product:{product_id}"
            )
        ])

    await message.answer(
        "🗑️ <b>Удаление товара</b>\n\n"
        "Выберите товар:",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=buttons
        )
    )


@dp.callback_query(F.data.startswith("delete_product:"))
async def delete_product_confirm(
    callback: CallbackQuery
):

    product_id = int(callback.data.split(":")[1])
    product = get_product(product_id)

    if not product:
        await callback.answer(
            "Товар не найден.",
            show_alert=True
        )
        return

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="❌ Да, удалить",
                    callback_data=f"delete_execute:{product_id}"
                )
            ],
            [
                InlineKeyboardButton(
                    text="↩️ Отмена",
                    callback_data="delete_cancel"
                )
            ]
        ]
    )

    await callback.message.edit_text(
        "⚠️ <b>Удаление товара</b>\n\n"
        f"Вы уверены, что хотите удалить:\n"
        f"<b>{product[1]}</b>?\n\n"
        "Остатки товара будут удалены.\n"
        "История продаж сохранится.",
        reply_markup=keyboard
    )

    await callback.answer()


@dp.callback_query(F.data.startswith("delete_execute:"))
async def delete_product_execute(
    callback: CallbackQuery
):

    product_id = int(callback.data.split(":")[1])

    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        SELECT name
        FROM products
        WHERE id = ?
    """, (product_id,))

    product = cur.fetchone()

    if not product:
        conn.close()
        await callback.answer(
            "Товар уже удалён.",
            show_alert=True
        )
        return

    name = product[0]

    cur.execute("""
        DELETE FROM products
        WHERE id = ?
    """, (product_id,))

    conn.commit()
    conn.close()

    await callback.message.edit_text(
        f"✅ Товар <b>{name}</b> удалён."
    )

    await callback.answer()


@dp.callback_query(F.data == "delete_cancel")
async def delete_cancel(
    callback: CallbackQuery
):

    await callback.message.edit_text(
        "↩️ Удаление отменено."
    )

    await callback.answer()


# =========================================================
# ДОСТУП
# =========================================================

async def access_start(
    message: Message,
    state: FSMContext
):

    if message.from_user.id != ADMIN_ID:
        await message.answer(
            "⛔ Только для администратора."
        )
        return

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="➕ Добавить сотрудника",
                    callback_data="access_add"
                )
            ],
            [
                InlineKeyboardButton(
                    text="👥 Список сотрудников",
                    callback_data="access_list"
                )
            ]
        ]
    )

    await message.answer(
        "🔐 <b>УПРАВЛЕНИЕ ДОСТУПОМ</b>\n\n"
        "Выберите действие:",
        reply_markup=keyboard
    )


@dp.callback_query(F.data == "access_add")
async def access_add(
    callback: CallbackQuery,
    state: FSMContext
):

    if callback.from_user.id != ADMIN_ID:
        await callback.answer(
            "⛔ Только для администратора.",
            show_alert=True
        )
        return

    await state.set_state(Access.user_id)

    await callback.message.edit_text(
        "➕ <b>Добавление сотрудника</b>\n\n"
        "Введите Telegram ID пользователя.\n\n"
        "Например:\n"
        "<code>123456789</code>"
    )

    await callback.answer()


@dp.message(Access.user_id)
async def access_user_id(
    message: Message,
    state: FSMContext
):

    if message.from_user.id != ADMIN_ID:
        await state.clear()
        return

    try:
        user_id = int(message.text.strip())
    except ValueError:
        await message.answer(
            "❌ Telegram ID должен состоять из цифр."
        )
        return

    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        INSERT OR IGNORE INTO allowed_users (user_id)
        VALUES (?)
    """, (user_id,))

    conn.commit()
    conn.close()

    await state.clear()

    await message.answer(
        "✅ <b>Доступ добавлен!</b>\n\n"
        f"Telegram ID: <code>{user_id}</code>\n\n"
        "Теперь этот пользователь должен открыть бота "
        "и нажать /start.",
        reply_markup=get_menu(message.from_user.id)
    )


@dp.callback_query(F.data == "access_list")
async def access_list(
    callback: CallbackQuery
):

    if callback.from_user.id != ADMIN_ID:
        await callback.answer(
            "⛔ Только для администратора.",
            show_alert=True
        )
        return

    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        SELECT user_id, created_at
        FROM allowed_users
        ORDER BY created_at DESC
    """)

    users = cur.fetchall()
    conn.close()

    if not users:

        await callback.message.edit_text(
            "👥 <b>Сотрудников нет.</b>",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="⬅️ Назад",
                            callback_data="access_back"
                        )
                    ]
                ]
            )
        )

        await callback.answer()
        return

    text = "👥 <b>СОТРУДНИКИ</b>\n\n"
    buttons = []

    for user_id, created_at in users:

        text += (
            f"👤 <code>{user_id}</code>\n"
            f"🕒 {created_at}\n\n"
        )

        buttons.append([
            InlineKeyboardButton(
                text=f"🗑️ Удалить {user_id}",
                callback_data=f"access_remove:{user_id}"
            )
        ])

    buttons.append([
        InlineKeyboardButton(
            text="⬅️ Назад",
            callback_data="access_back"
        )
    ])

    await callback.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=buttons
        )
    )

    await callback.answer()


@dp.callback_query(F.data.startswith("access_remove:"))
async def access_remove(
    callback: CallbackQuery
):

    if callback.from_user.id != ADMIN_ID:
        await callback.answer(
            "⛔ Только для администратора.",
            show_alert=True
        )
        return

    user_id = int(callback.data.split(":")[1])

    conn = get_db()
    cur = conn.cursor()

    cur.execute(
        "DELETE FROM allowed_users WHERE user_id = ?",
        (user_id,)
    )

    conn.commit()
    conn.close()

    await callback.answer("Доступ удалён.")

    await access_list(callback)


@dp.callback_query(F.data == "access_back")
async def access_back(
    callback: CallbackQuery
):

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="➕ Добавить сотрудника",
                    callback_data="access_add"
                )
            ],
            [
                InlineKeyboardButton(
                    text="👥 Список сотрудников",
                    callback_data="access_list"
                )
            ]
        ]
    )

    await callback.message.edit_text(
        "🔐 <b>УПРАВЛЕНИЕ ДОСТУПОМ</b>\n\n"
        "Выберите действие:",
        reply_markup=keyboard
    )

    await callback.answer()


# =========================================================
# ЗАПУСК
# =========================================================

async def main():

    print("🤖 Бот запускается...")

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
