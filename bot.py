import asyncio
import sqlite3

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    Message,
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery,
)


# ============================================================
# НАСТРОЙКИ
# ============================================================

TOKEN = "8816088914:AAGvK79K4bdhHzJWf42VwdW0u_ke7OEC9hc"
DB_NAME = "shop.db"


# ============================================================
# БАЗА ДАННЫХ
# ============================================================

conn = sqlite3.connect(DB_NAME)
cursor = conn.cursor()

cursor.execute("PRAGMA foreign_keys = ON")

cursor.execute("PRAGMA table_info(products)")
existing_columns = cursor.fetchall()

if existing_columns:
    column_names = [column[1] for column in existing_columns]

    if "size" in column_names:
        cursor.execute("DROP TABLE IF EXISTS sales")
        cursor.execute("DROP TABLE IF EXISTS product_sizes")
        cursor.execute("DROP TABLE IF EXISTS products")
        conn.commit()


# ============================================================
# ТОВАРЫ
# ============================================================

cursor.execute("""
CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    purchase_price REAL NOT NULL,
    sale_price REAL NOT NULL
)
""")


# ============================================================
# РАЗМЕРЫ И ОСТАТКИ
# ============================================================

cursor.execute("""
CREATE TABLE IF NOT EXISTS product_sizes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER NOT NULL,
    size TEXT NOT NULL,
    stock INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (product_id)
        REFERENCES products(id)
        ON DELETE CASCADE
)
""")


# ============================================================
# ПРОДАЖИ
# ============================================================

cursor.execute("""
CREATE TABLE IF NOT EXISTS sales (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER,
    product_name TEXT NOT NULL,
    size TEXT NOT NULL,
    quantity INTEGER NOT NULL,
    sale_price REAL NOT NULL,
    purchase_price REAL NOT NULL,
    profit REAL NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
""")

conn.commit()


# ============================================================
# BOT
# ============================================================

bot = Bot(
    token=TOKEN,
    default=DefaultBotProperties(
        parse_mode=ParseMode.HTML
    )
)

dp = Dispatcher()


# ============================================================
# ГЛАВНОЕ МЕНЮ
# ============================================================

main_menu = ReplyKeyboardMarkup(
    keyboard=[
        [
            KeyboardButton(text="📦 Наличие товаров"),
            KeyboardButton(text="🛒 Продажа")
        ],
        [
            KeyboardButton(text="📊 Статистика"),
            KeyboardButton(text="💰 Прибыль")
        ],
        [
            KeyboardButton(text="➕ Добавить товар"),
            KeyboardButton(text="📥 Пополнить остаток")
        ],
        [
            KeyboardButton(text="✏️ Изменить товар"),
            KeyboardButton(text="🗑️ Удалить товар")
        ],
    ],
    resize_keyboard=True
)


# ============================================================
# СОСТОЯНИЯ
# ============================================================

class AddProduct(StatesGroup):
    name = State()
    purchase_price = State()
    sale_price = State()
    sizes = State()
    stock = State()


class AddStock(StatesGroup):
    product_id = State()
    size_id = State()
    quantity = State()


class Sale(StatesGroup):
    product_id = State()
    size_id = State()
    quantity = State()
    sale_price = State()


class EditProduct(StatesGroup):
    product_id = State()
    field = State()
    value = State()


# ============================================================
# /START
# ============================================================

@dp.message(CommandStart())
async def start(message: Message, state: FSMContext):

    await state.clear()

    await message.answer(
        "👋 <b>Добро пожаловать!</b>\n\n"
        "Выберите действие:",
        reply_markup=main_menu
    )


# ============================================================
# 📦 НАЛИЧИЕ ТОВАРОВ
# ============================================================

@dp.message(F.text == "📦 Наличие товаров")
async def show_products(message: Message):

    cursor.execute("""
        SELECT id, name, sale_price
        FROM products
        ORDER BY id DESC
    """)

    products = cursor.fetchall()

    if not products:

        await message.answer(
            "📦 <b>Наличие товаров</b>\n\n"
            "Товаров пока нет.",
            reply_markup=main_menu
        )

        return

    text = "📦 <b>Наличие товаров</b>\n\n"

    for product_id, name, sale_price in products:

        cursor.execute("""
            SELECT size, stock
            FROM product_sizes
            WHERE product_id = ?
            ORDER BY id
        """, (product_id,))

        sizes = cursor.fetchall()

        total_stock = sum(stock for _, stock in sizes)

        text += f"🧥 <b>{name}</b>\n"

        for size, stock in sizes:
            text += f"{size} — {stock} шт.\n"

        text += f"📦 Всего: <b>{total_stock} шт.</b>\n"
        text += f"💰 Цена: <b>{sale_price:g} ₽</b>\n\n"

    await message.answer(
        text,
        reply_markup=main_menu
    )


# ============================================================
# ➕ ДОБАВИТЬ ТОВАР
# ============================================================

@dp.message(F.text == "➕ Добавить товар")
async def add_product_start(
    message: Message,
    state: FSMContext
):

    await state.clear()

    await state.set_state(
        AddProduct.name
    )

    await message.answer(
        "➕ <b>Добавление товара</b>\n\n"
        "Введите название товара.\n\n"
        "Например:\n"
        "<b>Пальто черное</b>"
    )


# ============================================================
# НАЗВАНИЕ
# ============================================================

@dp.message(AddProduct.name)
async def add_product_name(
    message: Message,
    state: FSMContext
):

    name = message.text.strip()

    if not name:

        await message.answer(
            "❌ Название не может быть пустым."
        )

        return

    await state.update_data(
        name=name
    )

    await state.set_state(
        AddProduct.purchase_price
    )

    await message.answer(
        "💵 <b>Закупочная цена</b>\n\n"
        "Введите закупочную цену за 1 шт.\n\n"
        "Например:\n"
        "<b>3500</b>"
    )


# ============================================================
# ЗАКУПОЧНАЯ ЦЕНА
# ============================================================

@dp.message(AddProduct.purchase_price)
async def add_product_purchase_price(
    message: Message,
    state: FSMContext
):

    try:

        price = float(
            message.text
            .replace(",", ".")
            .replace(" ", "")
        )

    except ValueError:

        await message.answer(
            "❌ Введите цену числом.\n\n"
            "Например: <b>3500</b>"
        )

        return

    if price <= 0:

        await message.answer(
            "❌ Цена должна быть больше 0."
        )

        return

    await state.update_data(
        purchase_price=price
    )

    await state.set_state(
        AddProduct.sale_price
    )

    await message.answer(
        "💰 <b>Цена продажи</b>\n\n"
        "Введите стандартную цену продажи за 1 шт.\n\n"
        "Например:\n"
        "<b>5000</b>"
    )


# ============================================================
# ЦЕНА ПРОДАЖИ
# ============================================================

@dp.message(AddProduct.sale_price)
async def add_product_sale_price(
    message: Message,
    state: FSMContext
):

    try:

        price = float(
            message.text
            .replace(",", ".")
            .replace(" ", "")
        )

    except ValueError:

        await message.answer(
            "❌ Введите цену числом.\n\n"
            "Например: <b>5000</b>"
        )

        return

    if price <= 0:

        await message.answer(
            "❌ Цена должна быть больше 0."
        )

        return

    await state.update_data(
        sale_price=price
    )

    await state.set_state(
        AddProduct.sizes
    )

    await message.answer(
        "📏 <b>Размеры</b>\n\n"
        "Введите размеры через запятую.\n\n"
        "Например:\n"
        "<b>S, M, L, XL</b>"
    )


# ============================================================
# РАЗМЕРЫ
# ============================================================

@dp.message(AddProduct.sizes)
async def add_product_sizes(
    message: Message,
    state: FSMContext
):

    sizes = [
        size.strip()
        for size in message.text.split(",")
        if size.strip()
    ]

    sizes = list(dict.fromkeys(sizes))

    if not sizes:

        await message.answer(
            "❌ Не удалось определить размеры.\n\n"
            "Введите например:\n"
            "<b>S, M, L, XL</b>"
        )

        return

    await state.update_data(
        sizes=sizes,
        current_size_index=0
    )

    await state.set_state(
        AddProduct.stock
    )

    await message.answer(
        f"📦 Введите остаток для размера "
        f"<b>{sizes[0]}</b>.\n\n"
        "Например:\n"
        "<b>5</b>"
    )


# ============================================================
# ОСТАТОК
# ============================================================

@dp.message(AddProduct.stock)
async def add_product_stock(
    message: Message,
    state: FSMContext
):

    try:

        stock = int(message.text)

    except ValueError:

        await message.answer(
            "❌ Введите целое число.\n\n"
            "Например: <b>5</b>"
        )

        return

    if stock < 0:

        await message.answer(
            "❌ Остаток не может быть отрицательным."
        )

        return

    data = await state.get_data()

    sizes = data["sizes"]
    current_index = data["current_size_index"]

    current_size = sizes[current_index]

    if current_index == 0:

        cursor.execute("""
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

        product_id = cursor.lastrowid

        await state.update_data(
            product_id=product_id
        )

    else:

        product_id = data["product_id"]

    cursor.execute("""
        INSERT INTO product_sizes (
            product_id,
            size,
            stock
        )
        VALUES (?, ?, ?)
    """, (
        product_id,
        current_size,
        stock
    ))

    conn.commit()

    next_index = current_index + 1

    if next_index < len(sizes):

        await state.update_data(
            current_size_index=next_index
        )

        await message.answer(
            f"📦 Введите остаток для размера "
            f"<b>{sizes[next_index]}</b>."
        )

    else:

        await state.clear()

        await message.answer(
            "✅ <b>Товар успешно добавлен!</b>\n\n"
            f"🧥 {data['name']}\n"
            f"💵 Закупка: <b>{data['purchase_price']:g} ₽</b>\n"
            f"💰 Цена: <b>{data['sale_price']:g} ₽</b>\n"
            f"📏 Размеров: <b>{len(sizes)}</b>",
            reply_markup=main_menu
        )


# ============================================================
# 📥 ПОПОЛНИТЬ ОСТАТОК
# ============================================================

@dp.message(F.text == "📥 Пополнить остаток")
async def add_stock_start(
    message: Message,
    state: FSMContext
):

    await state.clear()

    cursor.execute("""
        SELECT id, name
        FROM products
        ORDER BY name
    """)

    products = cursor.fetchall()

    if not products:

        await message.answer(
            "❌ Сначала добавьте товар.",
            reply_markup=main_menu
        )

        return

    buttons = []

    for product_id, name in products:

        buttons.append([
            InlineKeyboardButton(
                text=name,
                callback_data=f"stock_product_{product_id}"
            )
        ])

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=buttons
    )

    await message.answer(
        "📥 <b>Пополнение остатка</b>\n\n"
        "Выберите товар:",
        reply_markup=keyboard
    )


# ============================================================
# ТОВАР ДЛЯ ПОПОЛНЕНИЯ
# ============================================================

@dp.callback_query(
    F.data.startswith("stock_product_")
)
async def stock_choose_product(
    callback: CallbackQuery,
    state: FSMContext
):

    product_id = int(
        callback.data.replace(
            "stock_product_",
            ""
        )
    )

    await state.update_data(
        product_id=product_id
    )

    cursor.execute("""
        SELECT id, size, stock
        FROM product_sizes
        WHERE product_id = ?
        ORDER BY id
    """, (product_id,))

    sizes = cursor.fetchall()

    if not sizes:

        await callback.answer(
            "Размеров нет."
        )

        return

    buttons = []

    for size_id, size, stock in sizes:

        buttons.append([
            InlineKeyboardButton(
                text=f"{size} — {stock} шт.",
                callback_data=f"stock_size_{size_id}"
            )
        ])

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=buttons
    )

    await callback.message.edit_text(
        "📏 <b>Выберите размер:</b>",
        reply_markup=keyboard
    )

    await callback.answer()


# ============================================================
# РАЗМЕР ДЛЯ ПОПОЛНЕНИЯ
# ============================================================

@dp.callback_query(
    F.data.startswith("stock_size_")
)
async def stock_choose_size(
    callback: CallbackQuery,
    state: FSMContext
):

    size_id = int(
        callback.data.replace(
            "stock_size_",
            ""
        )
    )

    await state.update_data(
        size_id=size_id
    )

    await state.set_state(
        AddStock.quantity
    )

    cursor.execute("""
        SELECT size, stock
        FROM product_sizes
        WHERE id = ?
    """, (size_id,))

    result = cursor.fetchone()

    if not result:

        await callback.answer(
            "Размер не найден."
        )

        return

    size, stock = result

    await callback.message.edit_text(
        f"📏 Размер: <b>{size}</b>\n"
        f"📦 Сейчас: <b>{stock} шт.</b>\n\n"
        "Введите количество, которое нужно добавить:"
    )

    await callback.answer()


# ============================================================
# КОЛИЧЕСТВО ПОПОЛНЕНИЯ
# ============================================================

@dp.message(AddStock.quantity)
async def stock_quantity(
    message: Message,
    state: FSMContext
):

    try:

        quantity = int(message.text)

    except ValueError:

        await message.answer(
            "❌ Введите целое число."
        )

        return

    if quantity <= 0:

        await message.answer(
            "❌ Количество должно быть больше 0."
        )

        return

    data = await state.get_data()

    cursor.execute("""
        UPDATE product_sizes
        SET stock = stock + ?
        WHERE id = ?
    """, (
        quantity,
        data["size_id"]
    ))

    conn.commit()

    cursor.execute("""
        SELECT size, stock
        FROM product_sizes
        WHERE id = ?
    """, (data["size_id"],))

    result = cursor.fetchone()

    if not result:

        await message.answer(
            "❌ Ошибка."
        )

        await state.clear()

        return

    size, stock = result

    await state.clear()

    await message.answer(
        "✅ <b>Остаток пополнен!</b>\n\n"
        f"📏 Размер: {size}\n"
        f"➕ Добавлено: <b>{quantity} шт.</b>\n"
        f"📦 Теперь: <b>{stock} шт.</b>",
        reply_markup=main_menu
    )


# ============================================================
# 🛒 ПРОДАЖА
# ============================================================

@dp.message(F.text == "🛒 Продажа")
async def sale_start(
    message: Message,
    state: FSMContext
):

    await state.clear()

    cursor.execute("""
        SELECT id, name
        FROM products
        ORDER BY name
    """)

    products = cursor.fetchall()

    if not products:

        await message.answer(
            "❌ Товаров пока нет.",
            reply_markup=main_menu
        )

        return

    buttons = []

    for product_id, name in products:

        buttons.append([
            InlineKeyboardButton(
                text=name,
                callback_data=f"sale_product_{product_id}"
            )
        ])

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=buttons
    )

    await message.answer(
        "🛒 <b>Продажа</b>\n\n"
        "Выберите товар:",
        reply_markup=keyboard
    )


# ============================================================
# ТОВАР ПРИ ПРОДАЖЕ
# ============================================================

@dp.callback_query(
    F.data.startswith("sale_product_")
)
async def sale_choose_product(
    callback: CallbackQuery,
    state: FSMContext
):

    product_id = int(
        callback.data.replace(
            "sale_product_",
            ""
        )
    )

    await state.update_data(
        product_id=product_id
    )

    cursor.execute("""
        SELECT id, size, stock
        FROM product_sizes
        WHERE product_id = ?
        AND stock > 0
        ORDER BY id
    """, (product_id,))

    sizes = cursor.fetchall()

    if not sizes:

        await callback.message.edit_text(
            "❌ У этого товара нет размеров в наличии."
        )

        await callback.answer()

        return

    buttons = []

    for size_id, size, stock in sizes:

        buttons.append([
            InlineKeyboardButton(
                text=f"{size} — {stock} шт.",
                callback_data=f"sale_size_{size_id}"
            )
        ])

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=buttons
    )

    await callback.message.edit_text(
        "📏 <b>Выберите размер:</b>",
        reply_markup=keyboard
    )

    await callback.answer()


# ============================================================
# РАЗМЕР ПРИ ПРОДАЖЕ
# ============================================================

@dp.callback_query(
    F.data.startswith("sale_size_")
)
async def sale_choose_size(
    callback: CallbackQuery,
    state: FSMContext
):

    size_id = int(
        callback.data.replace(
            "sale_size_",
            ""
        )
    )

    await state.update_data(
        size_id=size_id
    )

    await state.set_state(
        Sale.quantity
    )

    cursor.execute("""
        SELECT size, stock
        FROM product_sizes
        WHERE id = ?
    """, (size_id,))

    result = cursor.fetchone()

    if not result:

        await callback.answer(
            "Размер не найден."
        )

        return

    size, stock = result

    await callback.message.edit_text(
        f"📏 Размер: <b>{size}</b>\n"
        f"📦 В наличии: <b>{stock} шт.</b>\n\n"
        "Введите количество для продажи:"
    )

    await callback.answer()


# ============================================================
# КОЛИЧЕСТВО ПРОДАЖИ
# ============================================================

@dp.message(Sale.quantity)
async def sale_quantity(
    message: Message,
    state: FSMContext
):

    try:

        quantity = int(message.text)

    except ValueError:

        await message.answer(
            "❌ Введите целое число.\n\n"
            "Например: <b>1</b>"
        )

        return

    if quantity <= 0:

        await message.answer(
            "❌ Количество должно быть больше 0."
        )

        return

    data = await state.get_data()

    cursor.execute("""
        SELECT stock, size, product_id
        FROM product_sizes
        WHERE id = ?
    """, (data["size_id"],))

    result = cursor.fetchone()

    if not result:

        await message.answer(
            "❌ Размер не найден."
        )

        return

    stock, size, product_id = result

    if quantity > stock:

        await message.answer(
            f"❌ Недостаточно товара.\n\n"
            f"В наличии: <b>{stock} шт.</b>"
        )

        return

    cursor.execute("""
        SELECT name, purchase_price, sale_price
        FROM products
        WHERE id = ?
    """, (product_id,))

    product = cursor.fetchone()

    if not product:

        await message.answer(
            "❌ Товар не найден."
        )

        return

    name, purchase_price, standard_sale_price = product

    await state.update_data(
        quantity=quantity,
        size=size,
        product_id=product_id,
        name=name,
        purchase_price=purchase_price,
        standard_sale_price=standard_sale_price
    )

    await state.set_state(
        Sale.sale_price
    )

    await message.answer(
        f"🧥 <b>{name}</b>\n"
        f"📏 Размер: <b>{size}</b>\n"
        f"🔢 Количество: <b>{quantity} шт.</b>\n\n"
        f"💰 По какой цене продаём 1 шт.?\n\n"
        f"Обычная цена: <b>{standard_sale_price:g} ₽</b>\n\n"
        "Введите фактическую цену продажи.\n"
        "Например: <b>4500</b>"
    )


# ============================================================
# ФАКТИЧЕСКАЯ ЦЕНА
# ============================================================

@dp.message(Sale.sale_price)
async def sale_actual_price(
    message: Message,
    state: FSMContext
):

    try:

        actual_sale_price = float(
            message.text
            .replace(",", ".")
            .replace(" ", "")
        )

    except ValueError:

        await message.answer(
            "❌ Введите цену числом.\n\n"
            "Например: <b>4500</b>"
        )

        return

    if actual_sale_price <= 0:

        await message.answer(
            "❌ Цена должна быть больше 0."
        )

        return

    data = await state.get_data()

    quantity = data["quantity"]
    size_id = data["size_id"]
    product_id = data["product_id"]

    name = data["name"]
    size = data["size"]

    purchase_price = data["purchase_price"]

    cursor.execute("""
        SELECT stock
        FROM product_sizes
        WHERE id = ?
    """, (size_id,))

    result = cursor.fetchone()

    if not result:

        await message.answer(
            "❌ Размер не найден."
        )

        await state.clear()

        return

    stock = result[0]

    if quantity > stock:

        await message.answer(
            f"❌ Остаток изменился.\n\n"
            f"Сейчас доступно: <b>{stock} шт.</b>"
        )

        await state.clear()

        return

    revenue = actual_sale_price * quantity

    profit = (
        actual_sale_price - purchase_price
    ) * quantity

    cursor.execute("""
        UPDATE product_sizes
        SET stock = stock - ?
        WHERE id = ?
    """, (
        quantity,
        size_id
    ))

    cursor.execute("""
        INSERT INTO sales (
            product_id,
            product_name,
            size,
            quantity,
            sale_price,
            purchase_price,
            profit
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        product_id,
        name,
        size,
        quantity,
        actual_sale_price,
        purchase_price,
        profit
    ))

    conn.commit()

    cursor.execute("""
        SELECT stock
        FROM product_sizes
        WHERE id = ?
    """, (size_id,))

    remaining_stock = cursor.fetchone()[0]

    await state.clear()

    await message.answer(
        "✅ <b>Продажа оформлена!</b>\n\n"
        f"🧥 Товар: <b>{name}</b>\n"
        f"📏 Размер: <b>{size}</b>\n"
        f"🔢 Количество: <b>{quantity} шт.</b>\n\n"
        f"💰 Цена продажи: <b>{actual_sale_price:g} ₽</b>\n"
        f"💵 Выручка: <b>{revenue:g} ₽</b>\n"
        f"📈 Прибыль: <b>{profit:g} ₽</b>\n\n"
        f"📦 Осталось: <b>{remaining_stock} шт.</b>",
        reply_markup=main_menu
    )


# ============================================================
# 📊 СТАТИСТИКА
# ============================================================

@dp.message(F.text == "📊 Статистика")
async def statistics(message: Message):

    cursor.execute("""
        SELECT
            COALESCE(SUM(quantity), 0),
            COALESCE(SUM(sale_price * quantity), 0),
            COALESCE(SUM(profit), 0)
        FROM sales
    """)

    total_quantity, revenue, profit = cursor.fetchone()

    cursor.execute("""
        SELECT COUNT(*)
        FROM products
    """)

    products_count = cursor.fetchone()[0]

    cursor.execute("""
        SELECT COALESCE(SUM(stock), 0)
        FROM product_sizes
    """)

    total_stock = cursor.fetchone()[0]

    await message.answer(
        "📊 <b>Статистика</b>\n\n"
        f"🧥 Товаров: <b>{products_count}</b>\n"
        f"📦 Остаток: <b>{total_stock} шт.</b>\n"
        f"🛒 Продано: <b>{total_quantity} шт.</b>\n"
        f"💵 Выручка: <b>{revenue:g} ₽</b>\n"
        f"📈 Прибыль: <b>{profit:g} ₽</b>",
        reply_markup=main_menu
    )


# ============================================================
# 💰 ПРИБЫЛЬ
# ============================================================

@dp.message(F.text == "💰 Прибыль")
async def profit_info(message: Message):

    cursor.execute("""
        SELECT COALESCE(SUM(profit), 0)
        FROM sales
    """)

    total_profit = cursor.fetchone()[0]

    cursor.execute("""
        SELECT
            COALESCE(SUM(sale_price * quantity), 0),
            COALESCE(SUM(purchase_price * quantity), 0)
        FROM sales
    """)

    revenue, purchase_cost = cursor.fetchone()

    await message.answer(
        "💰 <b>Прибыль</b>\n\n"
        f"💵 Выручка: <b>{revenue:g} ₽</b>\n"
        f"📦 Себестоимость: <b>{purchase_cost:g} ₽</b>\n"
        f"📈 Прибыль: <b>{total_profit:g} ₽</b>",
        reply_markup=main_menu
    )


# ============================================================
# ✏️ ИЗМЕНИТЬ ТОВАР
# ============================================================

@dp.message(F.text == "✏️ Изменить товар")
async def edit_product_start(
    message: Message,
    state: FSMContext
):

    await state.clear()

    cursor.execute("""
        SELECT id, name
        FROM products
        ORDER BY name
    """)

    products = cursor.fetchall()

    if not products:

        await message.answer(
            "❌ Товаров пока нет.",
            reply_markup=main_menu
        )

        return

    buttons = []

    for product_id, name in products:

        buttons.append([
            InlineKeyboardButton(
                text=name,
                callback_data=f"editproduct_{product_id}"
            )
        ])

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=buttons
    )

    await message.answer(
        "✏️ <b>Изменение товара</b>\n\n"
        "Выберите товар:",
        reply_markup=keyboard
    )


# ============================================================
# ВЫБОР ТОВАРА ДЛЯ ИЗМЕНЕНИЯ
# ============================================================

@dp.callback_query(
    F.data.startswith("editproduct_")
)
async def edit_choose_product(
    callback: CallbackQuery,
    state: FSMContext
):

    product_id = int(
        callback.data.replace(
            "editproduct_",
            ""
        )
    )

    await state.update_data(
        product_id=product_id
    )

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🧥 Название",
                    callback_data="editfield_name"
                )
            ],
            [
                InlineKeyboardButton(
                    text="💵 Закупочная цена",
                    callback_data="editfield_purchase"
                )
            ],
            [
                InlineKeyboardButton(
                    text="💰 Цена продажи",
                    callback_data="editfield_sale"
                )
            ],
        ]
    )

    await callback.message.edit_text(
        "✏️ <b>Что хотите изменить?</b>",
        reply_markup=keyboard
    )

    await callback.answer()


# ============================================================
# ИЗМЕНИТЬ НАЗВАНИЕ
# ============================================================

@dp.callback_query(
    F.data == "editfield_name"
)
async def edit_name(
    callback: CallbackQuery,
    state: FSMContext
):

    await state.update_data(
        field="name"
    )

    await state.set_state(
        EditProduct.value
    )

    await callback.message.edit_text(
        "🧥 <b>Введите новое название товара:</b>"
    )

    await callback.answer()


# ============================================================
# ИЗМЕНИТЬ ЗАКУПОЧНУЮ ЦЕНУ
# ============================================================

@dp.callback_query(
    F.data == "editfield_purchase"
)
async def edit_purchase(
    callback: CallbackQuery,
    state: FSMContext
):

    await state.update_data(
        field="purchase_price"
    )

    await state.set_state(
        EditProduct.value
    )

    await callback.message.edit_text(
        "💵 <b>Введите новую закупочную цену:</b>"
    )

    await callback.answer()


# ============================================================
# ИЗМЕНИТЬ ЦЕНУ ПРОДАЖИ
# ============================================================

@dp.callback_query(
    F.data == "editfield_sale"
)
async def edit_sale(
    callback: CallbackQuery,
    state: FSMContext
):

    await state.update_data(
        field="sale_price"
    )

    await state.set_state(
        EditProduct.value
    )

    await callback.message.edit_text(
        "💰 <b>Введите новую стандартную цену продажи:</b>"
    )

    await callback.answer()


# ============================================================
# СОХРАНЕНИЕ ИЗМЕНЕНИЯ
# ============================================================

@dp.message(EditProduct.value)
async def edit_value(
    message: Message,
    state: FSMContext
):

    data = await state.get_data()

    field = data["field"]
    product_id = data["product_id"]

    if field == "name":

        value = message.text.strip()

        if not value:

            await message.answer(
                "❌ Название не может быть пустым."
            )

            return

        cursor.execute("""
            UPDATE products
            SET name = ?
            WHERE id = ?
        """, (
            value,
            product_id
        ))

    else:

        try:

            value = float(
                message.text
                .replace(",", ".")
                .replace(" ", "")
            )

        except ValueError:

            await message.answer(
                "❌ Введите число."
            )

            return

        if value <= 0:

            await message.answer(
                "❌ Значение должно быть больше 0."
            )

            return

        cursor.execute(
            f"""
            UPDATE products
            SET {field} = ?
            WHERE id = ?
            """,
            (
                value,
                product_id
            )
        )

    conn.commit()

    await state.clear()

    await message.answer(
        "✅ <b>Товар успешно изменён.</b>",
        reply_markup=main_menu
    )


# ============================================================
# 🗑️ УДАЛИТЬ ТОВАР
# ============================================================

@dp.message(F.text == "🗑️ Удалить товар")
async def delete_product_start(
    message: Message
):

    cursor.execute("""
        SELECT id, name
        FROM products
        ORDER BY name
    """)

    products = cursor.fetchall()

    if not products:

        await message.answer(
            "❌ Товаров пока нет.",
            reply_markup=main_menu
        )

        return

    buttons = []

    for product_id, name in products:

        buttons.append([
            InlineKeyboardButton(
                text=f"🗑️ {name}",
                callback_data=f"delete_product_{product_id}"
            )
        ])

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=buttons
    )

    await message.answer(
        "🗑️ <b>Удаление товара</b>\n\n"
        "Выберите товар:",
        reply_markup=keyboard
    )


# ============================================================
# УДАЛЕНИЕ ТОВАРА
# ============================================================

@dp.callback_query(
    F.data.startswith("delete_product_")
)
async def delete_product(
    callback: CallbackQuery
):

    product_id = int(
        callback.data.replace(
            "delete_product_",
            ""
        )
    )

    cursor.execute("""
        SELECT name
        FROM products
        WHERE id = ?
    """, (product_id,))

    result = cursor.fetchone()

    if not result:

        await callback.answer(
            "Товар не найден."
        )

        return

    name = result[0]

    cursor.execute("""
        DELETE FROM products
        WHERE id = ?
    """, (product_id,))

    conn.commit()

    await callback.message.edit_text(
        f"✅ Товар <b>{name}</b> удалён."
    )

    await callback.answer()


# ============================================================
# ЗАПУСК
# ============================================================

async def main():

    print("Бот запущен...")

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())