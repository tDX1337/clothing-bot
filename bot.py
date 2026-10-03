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

DB_NAME = "shop.db"

ADMIN_ID = 5529220398


if not TOKEN:
    raise RuntimeError("BOT_TOKEN не найден в переменных окружения")


# =========================================================
# BOT / DISPATCHER
# =========================================================

bot = Bot(
    token=TOKEN,
    default=DefaultBotProperties(
        parse_mode=ParseMode.HTML
    )
)

dp = Dispatcher()


# =========================================================
# DATABASE
# =========================================================

def get_db():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():

    conn = get_db()
    cur = conn.cursor()

    # Проверяем старую структуру products
    cur.execute("PRAGMA table_info(products)")
    columns = [row["name"] for row in cur.fetchall()]

    # Если база от старой версии,
    # где размер находился прямо в products,
    # создаём новую структуру.
    if "size" in columns:

        cur.execute("DROP TABLE IF EXISTS sales")
        cur.execute("DROP TABLE IF EXISTS product_sizes")
        cur.execute("DROP TABLE IF EXISTS products")

    # Товары
    cur.execute("""
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            purchase_price REAL NOT NULL,
            sale_price REAL NOT NULL
        )
    """)

    # Размеры и остатки
    cur.execute("""
        CREATE TABLE IF NOT EXISTS product_sizes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id INTEGER NOT NULL,
            size TEXT NOT NULL,
            stock INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY(product_id)
                REFERENCES products(id)
                ON DELETE CASCADE
        )
    """)

    # Продажи
    cur.execute("""
        CREATE TABLE IF NOT EXISTS sales (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id INTEGER NOT NULL,
            product_name TEXT NOT NULL,
            size TEXT NOT NULL,
            quantity INTEGER NOT NULL,
            sale_price REAL NOT NULL,
            purchase_price REAL NOT NULL,
            profit REAL NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Пользователи с доступом
    cur.execute("""
        CREATE TABLE IF NOT EXISTS allowed_users (
            user_id INTEGER PRIMARY KEY,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Администратор всегда имеет доступ
    cur.execute(
        "INSERT OR IGNORE INTO allowed_users (user_id) VALUES (?)",
        (ADMIN_ID,)
    )

    conn.commit()
    conn.close()


# =========================================================
# ACCESS
# =========================================================

def has_access(user_id: int) -> bool:

    conn = get_db()
    cur = conn.cursor()

    cur.execute(
        "SELECT 1 FROM allowed_users WHERE user_id = ?",
        (user_id,)
    )

    result = cur.fetchone()

    conn.close()

    return result is not None


def is_admin(user_id: int) -> bool:
    return user_id == ADMIN_ID


class AccessMiddleware(BaseMiddleware):

    async def __call__(
        self,
        handler: Callable[[Any, Dict[str, Any]], Awaitable[Any]],
        event: Any,
        data: Dict[str, Any]
    ) -> Any:

        user = getattr(event, "from_user", None)

        if not user:
            return await handler(event, data)

        if not has_access(user.id):

            if isinstance(event, Message):

                await event.answer(
                    "🔒 <b>Доступ закрыт.</b>\n\n"
                    "У вас нет доступа к этому боту."
                )

            elif isinstance(event, CallbackQuery):

                await event.answer(
                    "🔒 Доступ закрыт.",
                    show_alert=True
                )

            return

        return await handler(event, data)


dp.message.outer_middleware(AccessMiddleware())
dp.callback_query.outer_middleware(AccessMiddleware())


# =========================================================
# KEYBOARDS
# =========================================================

def main_menu() -> ReplyKeyboardMarkup:

    return ReplyKeyboardMarkup(
        keyboard=[
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
        ],
        resize_keyboard=True
    )


def admin_menu() -> ReplyKeyboardMarkup:

    return ReplyKeyboardMarkup(
        keyboard=[
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
            [
                KeyboardButton(text="🔐 Доступ"),
            ],
        ],
        resize_keyboard=True
    )


def get_menu(user_id: int) -> ReplyKeyboardMarkup:

    if is_admin(user_id):
        return admin_menu()

    return main_menu()


# =========================================================
# ГЛОБАЛЬНАЯ НАВИГАЦИЯ
# =========================================================
# Эти кнопки имеют приоритет над любым FSM-состоянием.
#
# Например:
# Продажа → товар → размер → количество
# ↓
# нажимаем "Удалить товар"
#
# Продажа сбрасывается и открывается удаление товара.
# =========================================================

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


@dp.message(F.text.in_(MAIN_MENU_BUTTONS))
async def global_menu_navigation(
    message: Message,
    state: FSMContext
):

    # Сбрасываем текущее действие
    await state.clear()

    if message.text == "📦 Наличие товаров":

        await show_products(message)
        return

    if message.text == "🛒 Продажа":

        await sale_start(message, state)
        return

    if message.text == "📊 Статистика":

        await statistics(message)
        return

    if message.text == "💰 Прибыль":

        await profit(message)
        return

    if message.text == "➕ Добавить товар":

        await add_product_start(message, state)
        return

    if message.text == "📥 Пополнить остаток":

        await add_stock_start(message, state)
        return

    if message.text == "✏️ Изменить товар":

        await edit_product_start(message, state)
        return

    if message.text == "🗑️ Удалить товар":

        await delete_product_start(message)
        return

    if message.text == "🔐 Доступ":

        await access_menu(message, state)
        return


# =========================================================
# START
# =========================================================

@dp.message(CommandStart())
async def start(
    message: Message,
    state: FSMContext
):

    await state.clear()

    await message.answer(
        "👋 <b>Добро пожаловать!</b>\n\n"
        "Выберите нужный раздел:",
        reply_markup=get_menu(message.from_user.id)
    )


# =========================================================
# ACCESS MANAGEMENT
# =========================================================

class AccessControl(StatesGroup):

    add_user_id = State()


async def access_menu(
    message: Message,
    state: FSMContext
):

    await state.clear()

    if not is_admin(message.from_user.id):

        await message.answer(
            "⛔ У вас нет прав администратора."
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
            ],
            [
                InlineKeyboardButton(
                    text="🗑️ Удалить сотрудника",
                    callback_data="access_remove"
                )
            ],
        ]
    )

    await message.answer(
        "🔐 <b>Управление доступом</b>\n\n"
        "Выберите действие:",
        reply_markup=keyboard
    )


@dp.callback_query(F.data == "access_add")
async def access_add_start(
    callback: CallbackQuery,
    state: FSMContext
):

    if not is_admin(callback.from_user.id):

        await callback.answer(
            "⛔ Нет доступа.",
            show_alert=True
        )
        return

    await state.set_state(
        AccessControl.add_user_id
    )

    await callback.message.answer(
        "Введите Telegram ID сотрудника:"
    )

    await callback.answer()


@dp.message(AccessControl.add_user_id)
async def access_add_user(
    message: Message,
    state: FSMContext
):

    try:

        user_id = int(
            message.text.strip()
        )

    except ValueError:

        await message.answer(
            "❌ Telegram ID должен состоять только из цифр.\n\n"
            "Например: <code>123456789</code>"
        )
        return

    conn = get_db()
    cur = conn.cursor()

    cur.execute(
        "INSERT OR IGNORE INTO allowed_users (user_id) VALUES (?)",
        (user_id,)
    )

    conn.commit()
    conn.close()

    await state.clear()

    await message.answer(
        f"✅ Пользователь <code>{user_id}</code> получил доступ.",
        reply_markup=get_menu(message.from_user.id)
    )


@dp.callback_query(F.data == "access_list")
async def access_list(
    callback: CallbackQuery
):

    if not is_admin(callback.from_user.id):

        await callback.answer(
            "⛔ Нет доступа.",
            show_alert=True
        )
        return

    conn = get_db()
    cur = conn.cursor()

    cur.execute(
        "SELECT user_id FROM allowed_users ORDER BY user_id"
    )

    users = cur.fetchall()

    conn.close()

    if not users:

        text = "👥 Сотрудников пока нет."

    else:

        lines = [
            "👥 <b>Пользователи с доступом:</b>\n"
        ]

        for user in users:

            uid = user["user_id"]

            if uid == ADMIN_ID:

                lines.append(
                    f"👑 <code>{uid}</code> — администратор"
                )

            else:

                lines.append(
                    f"👤 <code>{uid}</code>"
                )

        text = "\n".join(lines)

    await callback.message.answer(text)

    await callback.answer()


@dp.callback_query(F.data == "access_remove")
async def access_remove_menu(
    callback: CallbackQuery
):

    if not is_admin(callback.from_user.id):

        await callback.answer(
            "⛔ Нет доступа.",
            show_alert=True
        )
        return

    conn = get_db()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT user_id
        FROM allowed_users
        WHERE user_id != ?
        """,
        (ADMIN_ID,)
    )

    users = cur.fetchall()

    conn.close()

    if not users:

        await callback.message.answer(
            "👥 Нет сотрудников, которых можно удалить."
        )

        await callback.answer()

        return

    buttons = []

    for user in users:

        uid = user["user_id"]

        buttons.append([
            InlineKeyboardButton(
                text=f"🗑️ {uid}",
                callback_data=f"access_delete:{uid}"
            )
        ])

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=buttons
    )

    await callback.message.answer(
        "Выберите сотрудника для удаления:",
        reply_markup=keyboard
    )

    await callback.answer()


@dp.callback_query(
    F.data.startswith("access_delete:")
)
async def access_delete_user(
    callback: CallbackQuery
):

    if not is_admin(callback.from_user.id):

        await callback.answer(
            "⛔ Нет доступа.",
            show_alert=True
        )
        return

    user_id = int(
        callback.data.split(":")[1]
    )

    if user_id == ADMIN_ID:

        await callback.answer(
            "❌ Нельзя удалить администратора.",
            show_alert=True
        )
        return

    conn = get_db()
    cur = conn.cursor()

    cur.execute(
        "DELETE FROM allowed_users WHERE user_id = ?",
        (user_id,)
    )

    conn.commit()
    conn.close()

    await callback.message.answer(
        f"✅ Доступ пользователя <code>{user_id}</code> удалён."
    )

    await callback.answer()


# =========================================================
# SHOW PRODUCTS
# =========================================================

async def show_products(
    message: Message
):

    conn = get_db()
    cur = conn.cursor()

    cur.execute(
        "SELECT * FROM products ORDER BY id DESC"
    )

    products = cur.fetchall()

    if not products:

        conn.close()

        await message.answer(
            "📦 <b>Товаров пока нет.</b>"
        )

        return

    text = "📦 <b>Наличие товаров:</b>\n\n"

    for product in products:

        cur.execute(
            """
            SELECT size, stock
            FROM product_sizes
            WHERE product_id = ?
            ORDER BY id
            """,
            (product["id"],)
        )

        sizes = cur.fetchall()

        text += (
            f"🛍️ <b>{product['name']}</b>\n"
            f"Закупка: {product['purchase_price']:.2f}\n"
            f"Продажа: {product['sale_price']:.2f}\n"
        )

        if sizes:

            for size in sizes:

                text += (
                    f"  • {size['size']}: "
                    f"<b>{size['stock']} шт.</b>\n"
                )

        else:

            text += (
                "  • Размеры не добавлены\n"
            )

        text += "\n"

    conn.close()

    await message.answer(text)


# =========================================================
# ADD PRODUCT
# =========================================================

class AddProduct(StatesGroup):

    name = State()
    purchase_price = State()
    sale_price = State()
    sizes = State()
    stock = State()


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
        "Введите название товара:"
    )


@dp.message(AddProduct.name)
async def add_product_name(
    message: Message,
    state: FSMContext
):

    await state.update_data(
        name=message.text.strip()
    )

    await state.set_state(
        AddProduct.purchase_price
    )

    await message.answer(
        "Введите закупочную цену:"
    )


@dp.message(AddProduct.purchase_price)
async def add_product_purchase_price(
    message: Message,
    state: FSMContext
):

    try:

        price = float(
            message.text.replace(",", ".")
        )

    except ValueError:

        await message.answer(
            "❌ Введите число.\n"
            "Например: <code>10.50</code>"
        )

        return

    if price < 0:

        await message.answer(
            "❌ Цена не может быть отрицательной."
        )

        return

    await state.update_data(
        purchase_price=price
    )

    await state.set_state(
        AddProduct.sale_price
    )

    await message.answer(
        "Введите цену продажи:"
    )


@dp.message(AddProduct.sale_price)
async def add_product_sale_price(
    message: Message,
    state: FSMContext
):

    try:

        price = float(
            message.text.replace(",", ".")
        )

    except ValueError:

        await message.answer(
            "❌ Введите число."
        )

        return

    if price < 0:

        await message.answer(
            "❌ Цена не может быть отрицательной."
        )

        return

    await state.update_data(
        sale_price=price
    )

    await state.set_state(
        AddProduct.sizes
    )

    await message.answer(
        "Введите размеры через запятую.\n\n"
        "Например:\n"
        "<code>S, M, L, XL</code>"
    )


# =========================================================
# РАЗМЕРЫ
# =========================================================

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

    if not sizes:

        await message.answer(
            "❌ Укажите хотя бы один размер."
        )

        return

    # Сохраняем размеры
    # и начинаем спрашивать остатки по одному
    await state.update_data(
        sizes=sizes,
        current_size_index=0,
        stocks=[]
    )

    await state.set_state(
        AddProduct.stock
    )

    await message.answer(
        f"Введите остаток для размера "
        f"<b>{sizes[0]}</b>:"
    )


# =========================================================
# ОСТАТОК КАЖДОГО РАЗМЕРА ОТДЕЛЬНО
# =========================================================

@dp.message(AddProduct.stock)
async def add_product_stock(
    message: Message,
    state: FSMContext
):

    data = await state.get_data()

    sizes = data["sizes"]

    current_index = data.get(
        "current_size_index",
        0
    )

    stocks = data.get(
        "stocks",
        []
    )

    current_size = sizes[current_index]

    # Проверяем остаток
    try:

        stock = int(
            message.text.strip()
        )

    except ValueError:

        await message.answer(
            f"❌ Введите целое число.\n\n"
            f"Остаток для размера "
            f"<b>{current_size}</b>:"
        )

        return

    if stock < 0:

        await message.answer(
            "❌ Остаток не может быть отрицательным.\n\n"
            f"Введите остаток для размера "
            f"<b>{current_size}</b>:"
        )

        return

    # Сохраняем остаток текущего размера
    stocks.append(stock)

    # Переходим к следующему размеру
    next_index = current_index + 1

    if next_index < len(sizes):

        await state.update_data(
            stocks=stocks,
            current_size_index=next_index
        )

        await message.answer(
            f"Введите остаток для размера "
            f"<b>{sizes[next_index]}</b>:"
        )

        return

    # =====================================================
    # ВСЕ РАЗМЕРЫ ГОТОВЫ
    # =====================================================

    conn = get_db()
    cur = conn.cursor()

    cur.execute(
        """
        INSERT INTO products
        (
            name,
            purchase_price,
            sale_price
        )
        VALUES (?, ?, ?)
        """,
        (
            data["name"],
            data["purchase_price"],
            data["sale_price"]
        )
    )

    product_id = cur.lastrowid

    # Добавляем каждый размер
    # с его отдельным остатком
    for size, size_stock in zip(
        sizes,
        stocks
    ):

        cur.execute(
            """
            INSERT INTO product_sizes
            (
                product_id,
                size,
                stock
            )
            VALUES (?, ?, ?)
            """,
            (
                product_id,
                size,
                size_stock
            )
        )

    conn.commit()
    conn.close()

    await state.clear()

    await message.answer(
        "✅ <b>Товар добавлен.</b>",
        reply_markup=get_menu(
            message.from_user.id
        )
    )


# =========================================================
# ADD STOCK
# =========================================================

class AddStock(StatesGroup):

    product = State()
    size = State()
    quantity = State()


async def add_stock_start(
    message: Message,
    state: FSMContext
):

    await state.clear()

    conn = get_db()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT id, name
        FROM products
        ORDER BY name
        """
    )

    products = cur.fetchall()

    conn.close()

    if not products:

        await message.answer(
            "📦 Нет товаров для пополнения."
        )

        return

    buttons = []

    for product in products:

        buttons.append([
            InlineKeyboardButton(
                text=product["name"],
                callback_data=f"stock_product:{product['id']}"
            )
        ])

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=buttons
    )

    await state.set_state(
        AddStock.product
    )

    await message.answer(
        "📥 Выберите товар:",
        reply_markup=keyboard
    )


@dp.callback_query(
    F.data.startswith("stock_product:")
)
async def add_stock_product(
    callback: CallbackQuery,
    state: FSMContext
):

    product_id = int(
        callback.data.split(":")[1]
    )

    await state.update_data(
        product_id=product_id
    )

    conn = get_db()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT size, stock
        FROM product_sizes
        WHERE product_id = ?
        """,
        (product_id,)
    )

    sizes = cur.fetchall()

    conn.close()

    if not sizes:

        await callback.message.answer(
            "❌ У этого товара нет размеров."
        )

        await state.clear()

        await callback.answer()

        return

    buttons = []

    for size in sizes:

        buttons.append([
            InlineKeyboardButton(
                text=f"{size['size']} — {size['stock']} шт.",
                callback_data=f"stock_size:{size['size']}"
            )
        ])

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=buttons
    )

    await state.set_state(
        AddStock.size
    )

    await callback.message.answer(
        "Выберите размер:",
        reply_markup=keyboard
    )

    await callback.answer()


@dp.callback_query(
    F.data.startswith("stock_size:")
)
async def add_stock_size(
    callback: CallbackQuery,
    state: FSMContext
):

    size = callback.data.split(
        ":",
        1
    )[1]

    await state.update_data(
        size=size
    )

    await state.set_state(
        AddStock.quantity
    )

    await callback.message.answer(
        f"Введите количество, которое нужно "
        f"добавить для размера <b>{size}</b>:"
    )

    await callback.answer()


@dp.message(AddStock.quantity)
async def add_stock_quantity(
    message: Message,
    state: FSMContext
):

    try:

        quantity = int(
            message.text
        )

    except ValueError:

        await message.answer(
            "❌ Введите целое число."
        )

        return

    if quantity <= 0:

        await message.answer(
            "❌ Количество должно быть больше нуля."
        )

        return

    data = await state.get_data()

    conn = get_db()
    cur = conn.cursor()

    cur.execute(
        """
        UPDATE product_sizes
        SET stock = stock + ?
        WHERE product_id = ?
        AND size = ?
        """,
        (
            quantity,
            data["product_id"],
            data["size"]
        )
    )

    conn.commit()
    conn.close()

    await state.clear()

    await message.answer(
        f"✅ Остаток увеличен на "
        f"<b>{quantity}</b> шт.",
        reply_markup=get_menu(
            message.from_user.id
        )
    )


# =========================================================
# SALE
# =========================================================

class Sale(StatesGroup):

    product = State()
    size = State()
    quantity = State()
    sale_price = State()


async def sale_start(
    message: Message,
    state: FSMContext
):

    await state.clear()

    conn = get_db()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT id, name
        FROM products
        ORDER BY name
        """
    )

    products = cur.fetchall()

    conn.close()

    if not products:

        await message.answer(
            "🛒 Нет товаров для продажи."
        )

        return

    buttons = []

    for product in products:

        buttons.append([
            InlineKeyboardButton(
                text=product["name"],
                callback_data=f"sale_product:{product['id']}"
            )
        ])

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=buttons
    )

    await state.set_state(
        Sale.product
    )

    await message.answer(
        "🛒 <b>Продажа</b>\n\n"
        "Выберите товар:",
        reply_markup=keyboard
    )


@dp.callback_query(
    F.data.startswith("sale_product:")
)
async def sale_product(
    callback: CallbackQuery,
    state: FSMContext
):

    product_id = int(
        callback.data.split(":")[1]
    )

    await state.update_data(
        product_id=product_id
    )

    conn = get_db()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT size, stock
        FROM product_sizes
        WHERE product_id = ?
        """,
        (product_id,)
    )

    sizes = cur.fetchall()

    conn.close()

    if not sizes:

        await callback.message.answer(
            "❌ У этого товара нет размеров."
        )

        await state.clear()

        await callback.answer()

        return

    buttons = []

    for size in sizes:

        buttons.append([
            InlineKeyboardButton(
                text=f"{size['size']} — {size['stock']} шт.",
                callback_data=f"sale_size:{size['size']}"
            )
        ])

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=buttons
    )

    await state.set_state(
        Sale.size
    )

    await callback.message.answer(
        "Выберите размер:",
        reply_markup=keyboard
    )

    await callback.answer()


@dp.callback_query(
    F.data.startswith("sale_size:")
)
async def sale_size(
    callback: CallbackQuery,
    state: FSMContext
):

    size = callback.data.split(
        ":",
        1
    )[1]

    await state.update_data(
        size=size
    )

    await state.set_state(
        Sale.quantity
    )

    await callback.message.answer(
        f"Введите количество проданного товара "
        f"размера <b>{size}</b>:"
    )

    await callback.answer()


@dp.message(Sale.quantity)
async def sale_quantity(
    message: Message,
    state: FSMContext
):

    try:

        quantity = int(
            message.text
        )

    except ValueError:

        await message.answer(
            "❌ Введите целое число."
        )

        return

    if quantity <= 0:

        await message.answer(
            "❌ Количество должно быть больше нуля."
        )

        return

    data = await state.get_data()

    conn = get_db()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT stock
        FROM product_sizes
        WHERE product_id = ?
        AND size = ?
        """,
        (
            data["product_id"],
            data["size"]
        )
    )

    result = cur.fetchone()

    conn.close()

    if not result:

        await message.answer(
            "❌ Размер не найден."
        )

        return

    if result["stock"] < quantity:

        await message.answer(
            f"❌ Недостаточно товара.\n\n"
            f"На складе: "
            f"<b>{result['stock']}</b> шт."
        )

        return

    await state.update_data(
        quantity=quantity
    )

    await state.set_state(
        Sale.sale_price
    )

    conn = get_db()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT name, sale_price
        FROM products
        WHERE id = ?
        """,
        (data["product_id"],)
    )

    product = cur.fetchone()

    conn.close()

    await message.answer(
        f"💰 Стандартная цена продажи: "
        f"<b>{product['sale_price']:.2f}</b>\n\n"
        "Укажите фактическую стоимость продажи "
        "за 1 единицу:"
    )


@dp.message(Sale.sale_price)
async def sale_price(
    message: Message,
    state: FSMContext
):

    try:

        price = float(
            message.text.replace(",", ".")
        )

    except ValueError:

        await message.answer(
            "❌ Введите число.\n"
            "Например: <code>25.50</code>"
        )

        return

    if price < 0:

        await message.answer(
            "❌ Цена не может быть отрицательной."
        )

        return

    data = await state.get_data()

    conn = get_db()
    cur = conn.cursor()

    # Получаем товар
    cur.execute(
        """
        SELECT name, purchase_price
        FROM products
        WHERE id = ?
        """,
        (data["product_id"],)
    )

    product = cur.fetchone()

    if not product:

        conn.close()

        await state.clear()

        await message.answer(
            "❌ Товар не найден."
        )

        return

    # Проверяем остаток ещё раз
    cur.execute(
        """
        SELECT stock
        FROM product_sizes
        WHERE product_id = ?
        AND size = ?
        """,
        (
            data["product_id"],
            data["size"]
        )
    )

    stock_result = cur.fetchone()

    if not stock_result:

        conn.close()

        await state.clear()

        await message.answer(
            "❌ Размер не найден."
        )

        return

    if stock_result["stock"] < data["quantity"]:

        conn.close()

        await message.answer(
            f"❌ Недостаточно товара.\n\n"
            f"На складе сейчас: "
            f"<b>{stock_result['stock']}</b> шт."
        )

        return

    # Считаем прибыль
    profit_value = (
        price - product["purchase_price"]
    ) * data["quantity"]

    # Уменьшаем остаток
    cur.execute(
        """
        UPDATE product_sizes
        SET stock = stock - ?
        WHERE product_id = ?
        AND size = ?
        """,
        (
            data["quantity"],
            data["product_id"],
            data["size"]
        )
    )

    # Записываем продажу
    cur.execute(
        """
        INSERT INTO sales
        (
            product_id,
            product_name,
            size,
            quantity,
            sale_price,
            purchase_price,
            profit
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            data["product_id"],
            product["name"],
            data["size"],
            data["quantity"],
            price,
            product["purchase_price"],
            profit_value
        )
    )

    conn.commit()
    conn.close()

    await state.clear()

    total = price * data["quantity"]

    await message.answer(
        "✅ <b>Продажа записана!</b>\n\n"
        f"Товар: <b>{product['name']}</b>\n"
        f"Размер: <b>{data['size']}</b>\n"
        f"Количество: <b>{data['quantity']} шт.</b>\n"
        f"Цена: <b>{price:.2f}</b>\n"
        f"Сумма: <b>{total:.2f}</b>\n"
        f"Прибыль: <b>{profit_value:.2f}</b>",
        reply_markup=get_menu(
            message.from_user.id
        )
    )


# =========================================================
# STATISTICS
# =========================================================

async def statistics(
    message: Message
):

    conn = get_db()
    cur = conn.cursor()

    cur.execute(
        "SELECT COUNT(*) AS count FROM sales"
    )

    sales_count = cur.fetchone()["count"]

    cur.execute(
        """
        SELECT
            COALESCE(SUM(quantity), 0) AS quantity,
            COALESCE(
                SUM(sale_price * quantity),
                0
            ) AS revenue,
            COALESCE(
                SUM(profit),
                0
            ) AS profit
        FROM sales
        """
    )

    stats = cur.fetchone()

    conn.close()

    await message.answer(
        "📊 <b>Статистика</b>\n\n"
        f"Количество продаж: "
        f"<b>{sales_count}</b>\n"
        f"Продано товаров: "
        f"<b>{stats['quantity']}</b> шт.\n"
        f"Выручка: "
        f"<b>{stats['revenue']:.2f}</b>\n"
        f"Прибыль: "
        f"<b>{stats['profit']:.2f}</b>"
    )


# =========================================================
# PROFIT
# =========================================================

async def profit(
    message: Message
):

    conn = get_db()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT
            COALESCE(SUM(profit), 0) AS profit
        FROM sales
        """
    )

    result = cur.fetchone()

    conn.close()

    await message.answer(
        "💰 <b>Прибыль</b>\n\n"
        f"Общая прибыль: "
        f"<b>{result['profit']:.2f}</b>"
    )


# =========================================================
# EDIT PRODUCT
# =========================================================

class EditProduct(StatesGroup):

    product = State()
    name = State()
    purchase_price = State()
    sale_price = State()


async def edit_product_start(
    message: Message,
    state: FSMContext
):

    await state.clear()

    conn = get_db()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT id, name
        FROM products
        ORDER BY name
        """
    )

    products = cur.fetchall()

    conn.close()

    if not products:

        await message.answer(
            "❌ Товаров пока нет."
        )

        return

    buttons = []

    for product in products:

        buttons.append([
            InlineKeyboardButton(
                text=product["name"],
                callback_data=f"edit_product:{product['id']}"
            )
        ])

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=buttons
    )

    await state.set_state(
        EditProduct.product
    )

    await message.answer(
        "✏️ Выберите товар для изменения:",
        reply_markup=keyboard
    )


@dp.callback_query(
    F.data.startswith("edit_product:")
)
async def edit_product_selected(
    callback: CallbackQuery,
    state: FSMContext
):

    product_id = int(
        callback.data.split(":")[1]
    )

    await state.update_data(
        product_id=product_id
    )

    await state.set_state(
        EditProduct.name
    )

    await callback.message.answer(
        "Введите новое название товара:"
    )

    await callback.answer()


@dp.message(EditProduct.name)
async def edit_product_name(
    message: Message,
    state: FSMContext
):

    await state.update_data(
        name=message.text.strip()
    )

    await state.set_state(
        EditProduct.purchase_price
    )

    await message.answer(
        "Введите новую закупочную цену:"
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

    except ValueError:

        await message.answer(
            "❌ Введите число."
        )

        return

    if price < 0:

        await message.answer(
            "❌ Цена не может быть отрицательной."
        )

        return

    await state.update_data(
        purchase_price=price
    )

    await state.set_state(
        EditProduct.sale_price
    )

    await message.answer(
        "Введите новую цену продажи:"
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

    except ValueError:

        await message.answer(
            "❌ Введите число."
        )

        return

    if price < 0:

        await message.answer(
            "❌ Цена не может быть отрицательной."
        )

        return

    data = await state.get_data()

    conn = get_db()
    cur = conn.cursor()

    cur.execute(
        """
        UPDATE products
        SET
            name = ?,
            purchase_price = ?,
            sale_price = ?
        WHERE id = ?
        """,
        (
            data["name"],
            data["purchase_price"],
            price,
            data["product_id"]
        )
    )

    conn.commit()
    conn.close()

    await state.clear()

    await message.answer(
        "✅ <b>Товар изменён.</b>",
        reply_markup=get_menu(
            message.from_user.id
        )
    )


# =========================================================
# DELETE PRODUCT
# =========================================================

async def delete_product_start(
    message: Message
):

    conn = get_db()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT id, name
        FROM products
        ORDER BY name
        """
    )

    products = cur.fetchall()

    conn.close()

    if not products:

        await message.answer(
            "🗑️ Удалять пока нечего."
        )

        return

    buttons = []

    for product in products:

        buttons.append([
            InlineKeyboardButton(
                text=f"🗑️ {product['name']}",
                callback_data=f"delete_product:{product['id']}"
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


@dp.callback_query(
    F.data.startswith("delete_product:")
)
async def delete_product(
    callback: CallbackQuery
):

    product_id = int(
        callback.data.split(":")[1]
    )

    conn = get_db()
    cur = conn.cursor()

    cur.execute(
        "SELECT name FROM products WHERE id = ?",
        (product_id,)
    )

    product = cur.fetchone()

    if not product:

        conn.close()

        await callback.answer(
            "❌ Товар не найден.",
            show_alert=True
        )

        return

    product_name = product["name"]

    cur.execute(
        "DELETE FROM products WHERE id = ?",
        (product_id,)
    )

    conn.commit()
    conn.close()

    await callback.message.answer(
        f"✅ Товар <b>{product_name}</b> удалён.",
        reply_markup=get_menu(
            callback.from_user.id
        )
    )

    await callback.answer()


# =========================================================
# START BOT
# =========================================================

async def main():

    init_db()

    print("Bot started")

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
