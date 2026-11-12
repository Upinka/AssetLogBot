import os
import asyncio
import logging
from datetime import date
from aiohttp import web
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
import aiosqlite
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet
from io import BytesIO

# --- НАСТРОЙКИ ---
BOT_TOKEN = os.environ.get("BOT_TOKEN") or "ВСТАВЬ_ТОКЕН"
WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "https://твое-имя.onrender.com")
WEBHOOK_PATH = f"/webhook/{BOT_TOKEN}"
FULL_WEBHOOK_URL = f"{WEBHOOK_URL}{WEBHOOK_PATH}"
PORT = int(os.environ.get("PORT", 8080))

logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
db = None

# --- СОСТОЯНИЯ (FSM) ---
class AddRecordStates(StatesGroup):
    waiting_asset = State()
    waiting_type = State()
    waiting_cost = State()
    waiting_comment = State()

# --- БАЗА ДАННЫХ ---
async def init_db():
    global db
    db = await aiosqlite.connect("assetlog.db")
    await db.execute("""CREATE TABLE IF NOT EXISTS records (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, asset TEXT, 
        date TEXT, type TEXT, cost REAL, comment TEXT)""")
    await db.commit()

# --- ГЕНЕРАЦИЯ PDF ---
async def generate_pdf(user_id: int) -> BytesIO:
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4)
    elements = []
    styles = getSampleStyleSheet()
    
    elements.append(Paragraph("Журнал обслуживания", styles['Title']))
    elements.append(Paragraph(f"Пользователь ID: {user_id}", styles['Normal']))
    elements.append(Paragraph(" ", styles['Normal'])) # Отступ

    async with db.execute("SELECT asset, date, type, cost, comment FROM records WHERE user_id = ?", (user_id,)) as cursor:
        rows = await cursor.fetchall()

    if not rows:
        elements.append(Paragraph("Записей пока нет.", styles['Normal']))
    else:
        data = [["Объект", "Дата", "Тип работ", "Стоимость (₽)", "Комментарий"]]
        for row in rows:
            data.append([str(row[0]), str(row[1]), str(row[2]), str(row[3]), str(row[4])])
        
        table = Table(data, colWidths=[80, 60, 100, 70, 150])
        table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 10),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
            ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
            ('GRID', (0, 0), (-1, -1), 1, colors.black)
        ]))
        elements.append(table)

    doc.build(elements)
    buffer.seek(0)
    return buffer

# --- ХЕНДЛЕРЫ ---
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    kb = types.ReplyKeyboardMarkup(keyboard=[
        [types.KeyboardButton(text="➕ Добавить запись")],
        [types.KeyboardButton(text="📄 Скачать PDF-паспорт")]
    ], resize_keyboard=True)
    await message.answer("👋 Добро пожаловать в AssetLog!\nВыберите действие:", reply_markup=kb)

@dp.message(F.text == "➕ Добавить запись")
async def start_add(message: types.Message, state: FSMContext):
    await message.answer("1️⃣ Введите название объекта (например: Kia Rio, Квартира):")
    await state.set_state(AddRecordStates.waiting_asset)

@dp.message(AddRecordStates.waiting_asset)
async def process_asset(message: types.Message, state: FSMContext):
    await state.update_data(asset=message.text)
    await message.answer("2️⃣ Введите тип работ (например: Замена масла, Ремонт):")
    await state.set_state(AddRecordStates.waiting_type)

@dp.message(AddRecordStates.waiting_type)
async def process_type(message: types.Message, state: FSMContext):
    await state.update_data(type=message.text)
    await message.answer("3️⃣ Введите стоимость (число, например: 5000):")
    await state.set_state(AddRecordStates.waiting_cost)

@dp.message(AddRecordStates.waiting_cost)
async def process_cost(message: types.Message, state: FSMContext):
    try:
        cost = float(message.text.replace(',', '.'))
    except ValueError:
        await message.answer("❌ Ошибка. Введите число (например: 5000).")
        return
    
    await state.update_data(cost=cost)
    await message.answer("4️⃣ Введите комментарий (или отправьте '-', чтобы пропустить):")
    await state.set_state(AddRecordStates.waiting_comment)

@dp.message(AddRecordStates.waiting_comment)
async def process_comment(message: types.Message, state: FSMContext):
    data = await state.get_data()
    comment = message.text if message.text != '-' else ""
    
    await db.execute(
        "INSERT INTO records (user_id, asset, date, type, cost, comment) VALUES (?, ?, ?, ?, ?, ?)",
        (message.from_user.id, data['asset'], date.today().isoformat(), data['type'], data['cost'], comment)
    )
    await db.commit()
    await message.answer("✅ Запись успешно сохранена!")
    await state.clear()

@dp.message(F.text == "📄 Скачать PDF-паспорт")
async def send_pdf(message: types.Message):
    await message.answer("⏳ Генерирую отчет...")
    pdf_buffer = await generate_pdf(message.from_user.id)
    await message.answer_document(
        types.BufferedInputFile(pdf_buffer.read(), filename=f"AssetLog_{message.from_user.id}.pdf"),
        caption="Ваш цифровой паспорт активов."
    )

# --- ЗАПУСК ---
async def on_startup():
    await init_db()
    await bot.set_webhook(FULL_WEBHOOK_URL, allowed_updates=dp.resolve_used_update_types())
    logging.info(f"Webhook set to {FULL_WEBHOOK_URL}")

async def main():
    await on_startup()
    app = web.Application()
    SimpleRequestHandler(dispatcher=dp, bot=bot).register(app, path=WEBHOOK_PATH)
    setup_application(app, dp, bot=bot)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())