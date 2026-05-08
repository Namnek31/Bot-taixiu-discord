import discord
from discord.ext import commands, tasks
import random
import time
import sqlite3
import asyncio
from datetime import timedelta

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

def parse_money(amount_str: str):
    """Chuyển đổi 1k, 1m, 1b thành con số cụ thể"""
    if isinstance(amount_str, (int, float)):
        return float(amount_str)
    
    amount_str = str(amount_str).lower().strip()
    
    # Định nghĩa các ký tự viết tắt
    multipliers = {
        'k': 1000,          # 1k = 1,000
        'm': 1000000,       # 1m = 1,000,000
        'b': 1000000000     # 1b = 1,000,000,000
    }
    
    try:
        # Nếu là "all", chúng ta sẽ xử lý riêng trong từng lệnh, 
        # nên ở đây trả về chuỗi "all" để lệnh đó tự hiểu.
        if amount_str == "all":
            return "all"
            
        # Kiểm tra xem ký tự cuối có nằm trong danh sách viết tắt không
        for char, multiplier in multipliers.items():
            if amount_str.endswith(char):
                return float(amount_str[:-1]) * multiplier
        
        # Nếu không có ký tự đặc biệt, chuyển về số bình thường
        return float(amount_str)
    except:
        return None
    
class HelpView(discord.ui.View):
    def __init__(self, ctx, ADMIN_IDS):
        super().__init__(timeout=60)
        self.ctx = ctx
        self.ADMIN_IDS = ADMIN_IDS
        self.current_page = 0
# ================== CẤU HÌNH DATABASE ==================
DB_NAME = "economy.db"
MAX_LOAN_PER_DAY = 100_000
INTEREST_TIME = 86400  # 24h
INTEREST_RATE = 0.1  # 10%
COOLDOWN_STEAL = 3600  # 1 tiếng tính bằng giây
steals_cooldown = {}   # Dictionary để lưu thời gian của từng người
COOLDOWN_WORK = 3600  # 1 tiếng tính bằng giây
work_cooldown = {}    # Dictionary lưu thời gian nhận tiền
crypto_market_data = {
    "bitcoin":  {"price": 65000.0, "volatility": 0.05}, # Biến động 5%
    "ethereum": {"price": 3500.0,  "volatility": 0.07}, # Biến động 7%
    "solana":   {"price": 140.0,   "volatility": 0.12}, # Biến động 12%
    "binance":  {"price": 580.0,   "volatility": 0.06}, # Biến động 6%
    "dogecoin": {"price": 15000.0,    "volatility": 0.25}, # Memecoin biến động 25%
    "pepe":     {"price": 50000.0, "volatility": 0.40}  # Memecoin biến động 40%
}

def init_db():
    # Mở kết nối
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    
    # 1. Bảng users
    c.execute('''CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    money INTEGER DEFAULT 1000000,
                    debt INTEGER DEFAULT 0,
                    bank INTEGER DEFAULT 0,
                    loan_today INTEGER DEFAULT 0,
                    last_loan_time INTEGER DEFAULT 0,
                    last_bank_time INTEGER DEFAULT 0,
                    loan_day TEXT DEFAULT '')''')
    
    # 2. Bảng crypto
    c.execute('''CREATE TABLE IF NOT EXISTS crypto (
                    user_id INTEGER PRIMARY KEY,
                    bitcoin REAL DEFAULT 0,
                    ethereum REAL DEFAULT 0,
                    binance REAL DEFAULT 0,
                    solana REAL DEFAULT 0,
                    dogecoin REAL DEFAULT 0,
                    pepe REAL DEFAULT 0)''')

    # 3. Bảng market_status
    c.execute('''CREATE TABLE IF NOT EXISTS market_status (
                    coin_name TEXT PRIMARY KEY,
                    current_price REAL,
                    available_supply REAL)''')

    # Nạp dữ liệu kho hàng (Sử dụng lệnh này TRƯỚC KHI đóng kết nối)
    coins = [
        ('bitcoin', 65000.0, 21.0),
        ('ethereum', 3500.0, 500.0),
        ('binance', 580.0, 1000.0),
        ('solana', 140.0, 2000.0),
        ('dogecoin', 0.15, 1000000.0),
        ('pepe', 0.00001, 100000000.0)
    ]
    c.executemany("INSERT OR IGNORE INTO market_status VALUES (?, ?, ?)", coins)

    # 4. Bảng mining
    c.execute('''CREATE TABLE IF NOT EXISTS mining (
                    user_id INTEGER PRIMARY KEY,
                    coin_name TEXT,
                    end_time REAL)''')
                    
    # LƯU VÀ ĐÓNG (Luôn nằm ở cuối cùng của hàm)
    conn.commit()



def get_user(uid):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    # Chỉ định rõ tên cột để tránh nhầm lẫn vị trí
    c.execute("SELECT user_id, money, debt, bank, loan_today, last_loan_time, last_bank_time, loan_day FROM users WHERE user_id = ?", (uid,))
    user = c.fetchone()
    
    today = time.strftime("%Y-%m-%d")
    
    if not user:
        # Nếu chưa có user, tạo mới với 100k tiền mặt
        c.execute("INSERT INTO users (user_id, money, debt, bank, loan_today, last_loan_time, last_bank_time, loan_day) VALUES (?, 1000000, 0, 0, 0, 0, 0, ?)", (uid, today))
        conn.commit()
        conn.close()
        return get_user(uid)
    
    user_dict = {
        "id": user[0],
        "money": user[1],
        "debt": user[2],
        "bank": user[3],
        "loan_today": user[4],
        "last_loan_time": user[5],
        "last_bank_time": user[6],
        "loan_day": user[7]
    }
    if user_dict["loan_day"] != today:
        c.execute("UPDATE users SET loan_today = 0, loan_day = ? WHERE user_id = ?", (today, uid))
        conn.commit()
        user_dict["loan_today"] = 0
        user_dict["loan_day"] = today
        
    conn.close()
    return user_dict

def update_user(uid, **kwargs):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    for key, value in kwargs.items():
        # Cập nhật bất kỳ cột nào được truyền vào (money, debt, bank, v.v.)
        c.execute(f"UPDATE users SET {key} = ? WHERE user_id = ?", (value, uid))
    conn.commit()
    conn.close()

race_status = {
    "is_running": False,
    "players": {},
    "start_time": 0
}
# ================== BOT READY ==================
@bot.event
async def on_ready():
    init_db()
    print(f"✅ Bot đã sẵn sàng: {bot.user.name}")
    check_interest.start()

# ================== LỆNH KINH TẾ ==================
@bot.command()
async def vidientu(ctx):
    # 1. Lấy thông tin tiền mặt từ bảng users
    u = get_user(ctx.author.id)
    money = u['money']
    bank = u['bank']
    debt = u['debt']

    # 2. Lấy thông tin coin từ bảng crypto
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT bitcoin, ethereum, solana, binance, dogecoin, pepe FROM crypto WHERE user_id = ?", (ctx.author.id,))
    res = c.fetchone()
    conn.close()

    # 3. Tính toán giá trị Crypto
    coin_names = ["bitcoin", "ethereum", "solana", "binance", "dogecoin", "pepe"]
    crypto_details = ""
    total_crypto_value = 0

    if res:
        for i, amount in enumerate(res):
            if amount > 0:
                coin_name = coin_names[i]
                current_price = crypto_market_data[coin_name]["price"]
                value = amount * current_price
                total_crypto_value += value
                crypto_details += f"• {coin_name.upper()}: `{amount:,.4f}` (~{int(value):,} $)\n"
    
    if not crypto_details:
        crypto_details = "*(Bạn chưa sở hữu đồng coin nào)*"

    # 4. Tạo Embed hiển thị cho chuyên nghiệp
    embed = discord.Embed(
        title=f"💳 VÍ ĐIỆN TỬ: {ctx.author.display_name}", 
        color=0x2ecc71,
        timestamp=ctx.message.created_at
    )
    
    embed.add_field(name="💵 Tiền mặt", value=f"**{money:,} VND**", inline=True)
    embed.add_field(name="🏦 Ngân hàng", value=f"**{bank:,} VND**", inline=True)
    
    if debt > 0:
        embed.add_field(name="🧧 Đang nợ", value=f"**-{debt:,} VND**", inline=True)

    embed.add_field(name="🪙 Tài sản Crypto", value=crypto_details, inline=False)
    
    # Tính tổng tài sản (Tiền mặt + Ngân hàng + Crypto - Nợ)
    total_assets = money + bank + total_crypto_value - debt
    embed.add_field(
        name="📊 Tổng giá trị tài sản (Ước tính)", 
        value=f"**{int(total_assets):,} VND**", 
        inline=False
    )

    embed.set_thumbnail(url=ctx.author.display_avatar.url)
    embed.set_footer(text="Gõ !crypto để xem bảng giá thị trường")

    await ctx.reply(embed=embed)

@bot.command()
async def taixiu(ctx, tien_input: str, chon: str):
    chon = chon.lower()
    if chon not in ["tai", "xiu"]:
        return await ctx.reply("❌ Chỉ chọn **tai** hoặc **xiu**")

    u = get_user(ctx.author.id)
    
    # 1. Hàm xử lý logic ký tự viết tắt (k, m, b) và "all"
    def parse_money_tx(val_str: str, user_money: int):
        val_str = val_str.lower().strip()
        if val_str == "all":
            return user_money
        
        multipliers = {'k': 1000, 'm': 1000000, 'b': 1000000000}
        try:
            for char, multi in multipliers.items():
                if val_str.endswith(char):
                    # Ví dụ: 1.5k -> 1.5 * 1000
                    return int(float(val_str[:-1]) * multi)
            return int(val_str) # Trường hợp nhập số bình thường
        except:
            return None

    tien = parse_money_tx(tien_input, u["money"])

    # 2. Kiểm tra điều kiện đầu vào
    if tien is None:
        return await ctx.reply("❌ Định dạng tiền không hợp lệ! (Ví dụ: 100k, 2m, 1b hoặc all)")
    
    if tien <= 0: 
        return await ctx.reply("❌ Tiền cược phải lớn hơn 0")
        
    if u["money"] < tien: 
        return await ctx.reply(f"❌ Bạn không đủ tiền (Hiện có: **{u['money']:,} VND**)")

    # 3. Lắc xúc xắc
    dice = [random.randint(1, 6) for _ in range(3)]
    tong = sum(dice)
    ketqua = "xiu" if tong <= 10 else "tai"

    # 4. Tính toán tiền thắng/thua
    if chon == ketqua:
        new_money = u["money"] + tien
        result_text = f"🎉 **WIN +{tien:,} VND**"
    else:
        new_money = u["money"] - tien
        result_text = f"💀 **LOSE -{tien:,} VND**"

    # 5. Cập nhật Database
    update_user(ctx.author.id, money=new_money)

    # 6. Trả lời kết quả
    await ctx.reply(
        f"🎲 Kết quả: **{dice[0]} | {dice[1]} | {dice[2]}**\n"
        f"👉 Tổng: **{tong}** → **{ketqua.upper()}**\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"{result_text}\n"
        f"💰 Số dư: **{new_money:,} VND**"
    )

@bot.command()
async def ck(ctx, member: discord.Member, tien_input: str): # Chuyển thành str để nhận k, m, b
    if member.id == ctx.author.id: 
        return await ctx.reply("❌ Bạn không thể tự chuyển tiền cho chính mình đâu ba!")

    # 1. Hàm xử lý ký tự viết tắt (k, m, b)
    def parse_transfer_money(val_str):
        val_str = val_str.lower().strip()
        multipliers = {'k': 1000, 'm': 1000000, 'b': 1000000000}
        try:
            for char, multi in multipliers.items():
                if val_str.endswith(char):
                    return int(float(val_str[:-1]) * multi)
            return int(val_str)
        except:
            return None

    tien = parse_transfer_money(tien_input)

    # 2. Kiểm tra điều kiện tiền tệ
    if tien is None or tien <= 0: 
        return await ctx.reply("❌ Số tiền không hợp lệ! (Ví dụ: 100k, 1.5m, 10b)")

    sender = get_user(ctx.author.id)
    receiver = get_user(member.id)

    if sender["money"] < tien: 
        return await ctx.reply(f"❌ Bạn không đủ tiền mặt! (Hiện có: **{sender['money']:,} VND**)")

    # 3. Thực hiện giao dịch
    update_user(ctx.author.id, money=sender["money"] - tien)
    update_user(member.id, money=receiver["money"] + tien)

    await ctx.reply(f"✅ Đã chuyển **{tien:,} VND** cho {member.mention}")

@bot.command()
async def vay(ctx, tien: int):
    u = get_user(ctx.author.id)
    if tien <= 0: return await ctx.reply("❌ Số tiền vay không hợp lệ")
    if u["loan_today"] + tien > MAX_LOAN_PER_DAY:
        return await ctx.reply(f"❌ Hạn mức còn lại: **{MAX_LOAN_PER_DAY - u['loan_today']:,} VND**")

    update_user(ctx.author.id, 
                money=u["money"] + tien, 
                debt=u["debt"] + tien, 
                loan_today=u["loan_today"] + tien,
                last_loan_time=int(time.time()))

    await ctx.reply(f"💸 Đã vay **{tien:,} VND**. Nợ hiện tại: **{u['debt'] + tien:,} VND**")

@bot.command()
async def trano(ctx):
    u = get_user(ctx.author.id)
    if u["debt"] <= 0: return await ctx.reply("❌ Bạn không có nợ")
    if u["money"] < u["debt"]: return await ctx.reply(f"❌ Cần thêm **{u['debt'] - u['money']:,} VND**")

    update_user(ctx.author.id, money=u["money"] - u["debt"], debt=0, last_loan_time=0)
    await ctx.reply("✅ Đã tất toán nợ nần!")

# ================== LÃI SUẤT & HỆ THỐNG ==================
@tasks.loop(minutes=5)
async def check_interest():
    now = int(time.time())
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT user_id, debt, last_loan_time FROM users WHERE debt > 0")
    debtors = c.fetchall()
    
    for uid, debt, last_time in debtors:
        if now - last_time >= INTEREST_TIME:
            new_debt = int(debt * (1 + INTEREST_RATE))
            c.execute("UPDATE users SET debt = ?, last_loan_time = ? WHERE user_id = ?", (new_debt, now, uid))
    
    conn.commit()
    conn.close()

@bot.command()
async def sotiendavay(ctx):
    u = get_user(ctx.author.id)
    await ctx.reply(f"📌 Bạn đang nợ **{u['debt']:,} VND**" if u["debt"] > 0 else "✅ Bạn không nợ tiền")
ADMIN_IDS = [1007146817336639518, 1281477499750055977, 1459769211504168982]

# ================== ADMIN COMMANDS ==================

@bot.command()
async def give(ctx, member: discord.Member, tien_input: str): # Chuyển thành str để nhận k, m, b
    # 1. Kiểm tra quyền Admin
    if ctx.author.id not in ADMIN_IDS:
        return await ctx.reply("❌ Bạn không có quyền sử dụng lệnh này!")

    # 2. Hàm xử lý ký tự viết tắt (k, m, b)
    def parse_admin_give(val_str):
        val_str = val_str.lower().strip()
        multipliers = {'k': 1000, 'm': 1000000, 'b': 1000000000}
        try:
            for char, multi in multipliers.items():
                if val_str.endswith(char):
                    return int(float(val_str[:-1]) * multi)
            return int(val_str)
        except:
            return None

    tien = parse_admin_give(tien_input)

    # 3. Kiểm tra điều kiện
    if tien is None or tien <= 0:
        return await ctx.reply("❌ Số tiền cấp không hợp lệ! (Ví dụ: 100k, 10m, 1b)")

    u = get_user(member.id)
    new_money = u["money"] + tien
    update_user(member.id, money=new_money)

    await ctx.reply(f"✅ Admin đã cấp **{tien:,} VND** cho {member.mention}.\n💰 Số dư mới: **{new_money:,} VND**")

@bot.command()
async def take(ctx, member: discord.Member, tien_input: str): # Chuyển thành str để nhận k, m, b
    # 1. Kiểm tra quyền Admin
    if ctx.author.id not in ADMIN_IDS:
        return await ctx.reply("❌ Bạn không có quyền sử dụng lệnh này!")

    u = get_user(member.id)
    
    # 2. Xử lý ký tự đặc biệt (Sử dụng lại logic parse tiền)
    def parse_admin_money(val_str):
        val_str = val_str.lower().strip()
        multipliers = {'k': 1000, 'm': 1000000, 'b': 1000000000}
        try:
            for char, multi in multipliers.items():
                if val_str.endswith(char):
                    return int(float(val_str[:-1]) * multi)
            return int(val_str)
        except:
            return None

    tien = parse_admin_money(tien_input)

    if tien is None or tien <= 0:
        return await ctx.reply("❌ Số tiền thu hồi không hợp lệ! (Ví dụ: 100k, 1m, 1b)")

    # 3. Thực hiện thu hồi
    new_money = max(0, u["money"] - tien)
    update_user(member.id, money=new_money)

    await ctx.reply(f"🔥 Admin đã thu hồi **{tien:,} VND** từ {member.mention}.\n💰 Số dư còn lại: **{new_money:,} VND**")

@bot.command()
async def gaino(ctx, member: discord.Member, no_input: str): # Chuyển thành str để nhận k, m, b
    # 1. Kiểm tra quyền Admin
    if ctx.author.id not in ADMIN_IDS:
        return await ctx.reply("❌ Bạn không có quyền sử dụng lệnh này!")

    u = get_user(member.id)
    
    # 2. Hàm xử lý ký tự viết tắt
    def parse_debt_money(val_str):
        val_str = val_str.lower().strip()
        multipliers = {'k': 1000, 'm': 1000000, 'b': 1000000000}
        try:
            for char, multi in multipliers.items():
                if val_str.endswith(char):
                    return int(float(val_str[:-1]) * multi)
            return int(val_str)
        except:
            return None

    so_no = parse_debt_money(no_input)

    # 3. Kiểm tra điều kiện
    if so_no is None or so_no <= 0:
        return await ctx.reply("❌ Số nợ không hợp lệ! (Ví dụ: 100k, 1m, 1b)")

    # 4. Cập nhật nợ
    new_debt = u["debt"] + so_no
    update_user(member.id, debt=new_debt)

    await ctx.reply(
        f"💸 **ADMIN ĐÃ GÀI NỢ!**\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 Đối tượng: {member.mention}\n"
        f"📉 Khoản nợ vừa gán: **{so_no:,} VND**\n"
        f"🏦 Tổng nợ hiện tại: **{new_debt:,} VND**\n"
        f"⚠️ *Hãy trả nợ sớm trước khi lãi suất 10% ập đến!*"
    )

@bot.command()
async def truythuno(ctx, member: discord.Member = None):
    # Nếu không tag ai, thì tự truy thu chính mình
    target = member if member else ctx.author
    
    # Kiểm tra quyền: Nếu truy thu người khác thì phải là Admin
    if member and ctx.author.id not in ADMIN_IDS:
        return await ctx.reply("❌ Bạn không có quyền cưỡng chế truy thu nợ của người khác!")

    u = get_user(target.id)
    no_hien_tai = u["debt"]

    if no_hien_tai <= 0:
        return await ctx.reply(f"✅ {target.display_name} hiện không có nợ!")

    vi_tien = u["money"]
    ngan_hang = u["bank"]
    tong_tai_san = vi_tien + ngan_hang
    
    da_tra = 0
    
    # Bắt đầu trừ tiền
    if tong_tai_san <= no_hien_tai:
        # Nếu tổng tài sản không đủ trả hết nợ
        da_tra = tong_tai_san
        moi_money = 0
        moi_bank = 0
        moi_debt = no_hien_tai - da_tra
    else:
        # Nếu đủ trả hết nợ
        da_tra = no_hien_tai
        moi_debt = 0
        # Ưu tiên trừ tiền mặt trước, còn lại trừ ngân hàng
        if vi_tien >= no_hien_tai:
            moi_money = vi_tien - no_hien_tai
            moi_bank = ngan_hang
        else:
            moi_money = 0
            moi_bank = ngan_hang - (no_hien_tai - vi_tien)

    # Cập nhật Database
    update_user(target.id, money=moi_money, bank=moi_bank, debt=moi_debt)

    # Gửi thông báo
    embed_content = f"🏦 **LỆNH TRUY THU NỢ TỰ ĐỘNG**\n"
    embed_content += "━━━━━━━━━━━━━━━━━━━━━━━━\n"
    embed_content += f"👤 Đối tượng: **{target.display_name}**\n"
    embed_content += f"💸 Đã thu hồi: `{da_tra:,} VND`\n"
    embed_content += f"📉 Nợ còn lại: **{moi_debt:,} VND**\n"
    embed_content += "━━━━━━━━━━━━━━━━━━━━━━━━\n"
    embed_content += f"💰 Ví: `{moi_money:,}` | 🏦 Bank: `{moi_bank:,}`"
    
    await ctx.reply(embed_content)

# ================== LỆNH CƯỚP TIỀN ==================
@bot.command()
async def tromtien(ctx, member: discord.Member):
   #1. Không thể cướp chính mình
    if member.id == ctx.author.id:
        return await ctx.reply("❌ Bạn không thể tự cướp chính mình!")

    # 2. Không thể cướp Bot
    if member.bot:
        return await ctx.reply("❌ Bạn không thể cướp tiền của Bot!")

    # 3. Kiểm tra Cooldown
    now = time.time()
    if ctx.author.id in steals_cooldown:
        con_lai = steals_cooldown[ctx.author.id] - now
        if con_lai > 0:
            return await ctx.reply(f"⏳ Hãy đợi thêm **{int(con_lai//60)} phút {int(con_lai%60)} giây**.")

    # 4. Lấy dữ liệu 2 bên
    thang_cuop = get_user(ctx.author.id)
    nan_nhan = get_user(member.id)

    if nan_nhan["money"] < 1000:
        return await ctx.reply(f"❌ {member.mention} quá nghèo!")

    # Cập nhật cooldown ngay
    steals_cooldown[ctx.author.id] = now + COOLDOWN_STEAL
    
    # 5. Tính xác suất thất bại 60%
    if random.random() < 0.60:
        # THẤT BẠI: Tính tiền phạt 30-50%
        phan_tram_phat = random.randint(30, 50) / 100
        so_tien_phat = int(thang_cuop["money"] * phan_tram_phat)
        
        # Nếu không có tiền trong ví để phạt (ví dụ ví = 0), hoặc nộp phạt xong vẫn đen
        # Code sẽ cộng thêm 50,000 vào nợ (debt) như một khoản án phí/tiền tại ngoại
        phi_an_ninh = 50000 
        
        new_money = max(0, thang_cuop["money"] - so_tien_phat)
        new_debt = thang_cuop["debt"] + phi_an_ninh
        
        # Cập nhật cả tiền mặt và nợ vào database
        update_user(ctx.author.id, money=new_money, debt=new_debt)
        
        return await ctx.reply(
            f"🚔 **Thất bại!** Bạn đã bị cảnh sát tóm.\n"
            f"💸 Phạt tiền mặt: **{so_tien_phat:,} VND**.\n"
            f"⚠️ Vì tội danh nguy hiểm, bạn bị tính thêm **{phi_an_ninh:,} VND** vào tiền nợ!\n"
            f"📌 Tổng nợ hiện tại: **{new_debt:,} VND**"
        )

    # 6. Thành công: Cướp từ 20-25%
   # Thành công: Lấy 20-25% nhưng không quá 100,000 VND
    so_tien_cuop = min(int(nan_nhan["money"] * (random.randint(20, 25) / 100)), 100000)
    update_user(ctx.author.id, money=thang_cuop["money"] + so_tien_cuop)
    update_user(member.id, money=nan_nhan["money"] - so_tien_cuop)
    await ctx.reply(f"🥷 Thành công! Bạn đã cướp được **{so_tien_cuop:,} VND** từ {member.mention}!")
# ================== CÁCH CHƠI ==================
# --- 1. ĐOẠN CLASS XỬ LÝ (Giữ nguyên) ---
class HelpPagination(discord.ui.View):
    def __init__(self, ctx, pages, current_page=0):
        super().__init__(timeout=60)
        self.ctx = ctx
        self.pages = pages
        self.current_page = current_page

    def create_embed(self):
        p = self.pages[self.current_page]
        embed = discord.Embed(title=p["title"], description=p["desc"], color=p["color"])
        embed.set_footer(text=f"Trang {self.current_page + 1}/{len(self.pages)}")
        return embed

    @discord.ui.button(label="◀️ Trước", style=discord.ButtonStyle.gray)
    async def prev(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.ctx.author.id: return
        self.current_page = (self.current_page - 1) % len(self.pages)
        await interaction.response.edit_message(embed=self.create_embed(), view=self)

    @discord.ui.button(label="Tiếp ▶️", style=discord.ButtonStyle.gray)
    async def next(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.ctx.author.id: return
        self.current_page = (self.current_page + 1) % len(self.pages)
        await interaction.response.edit_message(embed=self.create_embed(), view=self)

# --- 2. LỆNH !CACHCHOI (Đã cách dòng từng lệnh) ---
@bot.command()
async def cachchoi(ctx):
    pages = [
        {
            "title": "💰 CƠ BẢN (Trang 1/5)",
            "desc": (
                "* **!vidientu**: Xem toàn bộ tài sản hiện có.\n\n"
                "* **!lamviec**: Làm việc kiếm tiền (1h/lần).\n\n"
                "* **!ck <@user> <tiền>**: Chuyển tiền mặt.\n\n"
                "* **!bxhtx**: Xem bảng xếp hạng đại gia."
            ),
            "color": 0x3498db
        },
        {
            "title": "📈 THỊ TRƯỜNG CRYPTO (Trang 2/5)",
            "desc": (
                "* **!crypto**: Xem bảng giá sàn giao dịch.\n\n"
                "* **!muacoin <tên> <sl>**: Mua coin vào kho.\n\n"
                "* **!bancoin <tên> <sl>**: Bán coin lấy tiền.\n\n"
                "* **!dao <tên>**: Thuê máy đào (2,000,000 VND).\n\n"
                "> 💡 *Mẹo: Mua thấp bán cao để làm giàu!*"
            ),
            "color": 0xf1c40f
        },
        {
            "title": "🎲 GIẢI TRÍ & CÁ CƯỢC (Trang 3/5)",
            "desc": (
                "* **!taixiu <tiền> <t/x>**: Thử vận may xúc xắc.\n\n"
                "* **!blackjack <tiền>**: Đánh bài Xì Dách.\n\n"
                "* **!baucua <tiền> <lv>**: Đặt cược Bầu Cua.\n\n"
                "* **!bongda**: Danh sách các trận đấu.\n\n"
                "* **!cuoc <trận> <tiền> <đội>**: Đặt cược bóng đá.\n\n"  # Thêm \n\n ở đây
                "* **!baccarat <tiền> <cửa>**: Cửa đặt: `player`, `banker`, `tie`."
            ),
            "color": 0x9b59b6
        },
        {
            "title": "🏦 NGÂN HÀNG & TỘI PHẠM (Trang 4/5)",
            "desc": (
                "**🥷 TỘI PHẠM**\n"
                "* **!tromtien <@user>**: Cướp tiền đối phương.\n\n"
                "**🏦 NGÂN HÀNG**\n"
                "* **!guitien <tiền>**: Gửi tiết kiệm lãi 5%.\n\n"
                "* **!ruttien <tiền>**: Rút tiền về ví.\n\n"
                "* **!taikhoan**: Kiểm tra số dư & lãi suất.\n\n"
                "* **!vay / !trano / !topno**: Tín dụng nợ."
            ),
            "color": 0xe67e22
        }
    ]

    if ctx.author.id in ADMIN_IDS:
        pages.append({
            "title": "👑 QUYỀN ADMIN (Trang 5/5)",
            "desc": (
                "**🛠️ LỆNH CƯỠNG CHẾ**\n\n"
                "* **!give**: Cấp tiền cho mem.\n\n"
                "* **!take**: Thu hồi tiền.\n\n"
                "* **!gaino**: Gán nợ cho mem.\n\n"
                "* **!truythuno**: Cưỡng chế nợ.\n\n"
                "* **!resetdb**: Reset dữ liệu."
            ),
            "color": 0xff0000
        })

    view = HelpPagination(ctx, pages)
    await ctx.send(embed=view.create_embed(), view=view)
# ================== LỆNH KIẾM TIỀN ==================
@bot.command()
async def lamviec(ctx):
    now = time.time()
    uid = ctx.author.id

    # 1. Kiểm tra Cooldown (1 tiếng)
    if uid in work_cooldown:
        con_lai = work_cooldown[uid] - now
        if con_lai > 0:
            phut = int(con_lai // 60)
            giay = int(con_lai % 60)
            return await ctx.reply(f"⏳ Bạn đã kiệt sức! Hãy nghỉ ngơi thêm **{phut} phút {giay} giây**.")

    # 2. Ngẫu nhiên số tiền từ 0 -> 500,000 VND
    so_tien = random.randint(1000, 500000) # Đặt tối thiểu 1000 cho đỡ hụt hẫng
    
    u = get_user(uid)
    new_money = u["money"] + so_tien
    
    # 3. Cập nhật Database và Cooldown
    update_user(uid, money=new_money)
    work_cooldown[uid] = now + COOLDOWN_WORK

    # Các công việc ngẫu nhiên cho sinh động
    cong_viec = [
        "đi bốc vác", "chạy Grab", "bán vé số", "lập trình dạo", 
        "đi rửa bát", "nuôi cá lòng hồ", "nhặt ve chai"
    ]
    viec_da_lam = random.choice(cong_viec)

    await ctx.reply(
        f"🛠️ Bạn đã **{viec_da_lam}** và kiếm được **{so_tien:,} VND**\n"
        f"💰 Số dư hiện tại: **{new_money:,} VND**\n"
        f"⏰ Quay lại sau 1 tiếng nhé!"
    )

# ================== NGÂN HÀNG (BANK) ==================

@bot.command()
async def guitien(ctx, tien_input: str): # Chuyển thành str để nhận k, m, b và all
    u = get_user(ctx.author.id)

    # 1. Hàm xử lý logic ký tự viết tắt và "all"
    def parse_bank_money(val_str, user_money):
        val_str = val_str.lower().strip()
        if val_str == "all":
            return user_money
        
        multipliers = {'k': 1000, 'm': 1000000, 'b': 1000000000}
        try:
            for char, multi in multipliers.items():
                if val_str.endswith(char):
                    return int(float(val_str[:-1]) * multi)
            return int(val_str)
        except:
            return None

    tien = parse_bank_money(tien_input, u["money"])

    # 2. Kiểm tra điều kiện
    if tien is None or tien <= 0:
        return await ctx.reply("❌ Số tiền gửi không hợp lệ! (Ví dụ: 100k, 1m, 1b hoặc all)")
    
    if u["money"] < tien:
        return await ctx.reply(f"❌ Bạn không đủ tiền mặt để gửi! (Hiện có: **{u['money']:,} VND**)")

    # 3. Thực hiện gửi tiền
    now = int(time.time())
    # Lưu ý: Hệ thống của bạn tính lãi dựa trên last_bank_time, 
    # việc gửi thêm tiền sẽ reset mốc thời gian tính lãi mới.
    new_bank = u["bank"] + tien
    
    update_user(ctx.author.id, 
                money=u["money"] - tien, 
                bank=new_bank, 
                last_bank_time=now)

    await ctx.reply(
        f"🏦 **NGÂN HÀNG TRUNG ƯƠNG**\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"✅ Đã gửi thành công: **{tien:,} VND**\n"
        f"🔒 Tổng tích lũy trong Bank: **{new_bank:,} VND**\n"
        f"📈 Lãi suất hiện tại: **5%/giờ**"
    )
@bot.command()
async def ruttien(ctx, tien_input: str): # Chuyển thành str để nhận k, m, b và all
    u = get_user(ctx.author.id)
    
    if u["bank"] <= 0: 
        return await ctx.reply("❌ Ngân hàng của bạn đang trống rỗng, có gì đâu mà rút ba!")

    # 1. Hàm xử lý logic ký tự viết tắt và "all"
    def parse_withdraw_money(val_str, user_bank):
        val_str = val_str.lower().strip()
        if val_str == "all":
            return user_bank
        
        multipliers = {'k': 1000, 'm': 1000000, 'b': 1000000000}
        try:
            for char, multi in multipliers.items():
                if val_str.endswith(char):
                    return int(float(val_str[:-1]) * multi)
            return int(val_str)
        except:
            return None

    tien = parse_withdraw_money(tien_input, u["bank"])

    # 2. Kiểm tra điều kiện rút tiền
    if tien is None or tien <= 0:
        return await ctx.reply("❌ Số tiền rút không hợp lệ! (Ví dụ: 100k, 1m, 1b hoặc all)")
    
    if tien > u["bank"]:
        return await ctx.reply(f"❌ Bạn không đủ tiền trong Bank! (Hiện có: **{u['bank']:,} VND**)")

    # 3. Thực hiện rút tiền
    new_bank = u["bank"] - tien
    new_money = u["money"] + tien
    
    # Cập nhật đồng thời cả ví và ngân hàng vào Database
    update_user(ctx.author.id, money=new_money, bank=new_bank)
    
    await ctx.reply(
        f"🏦 **NGÂN HÀNG TRUNG ƯƠNG**\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"✅ Rút thành công: **{tien:,} VND**\n"
        f"💰 Tiền mặt hiện có: **{new_money:,} VND**\n"
        f"🔒 Còn lại trong Bank: **{new_bank:,} VND**"
    )
    # 1. Logic tính lãi nợ (giữ nguyên code cũ của bạn)
    # ... (phần code tính nợ cũ) ...

    # 2. Logic tính lãi ngân hàng (5% mỗi 3600 giây)
@tasks.loop(minutes=60)
async def check_interest():
    now = int(time.time())
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    
    # Logic lãi nợ
    c.execute("SELECT user_id, debt, last_loan_time FROM users WHERE debt > 0")
    debtors = c.fetchall()
    for uid, debt, last_time in debtors:
        if now - last_time >= 86400: # 24h
            new_debt = int(debt * 1.05)
            c.execute("UPDATE users SET debt = ?, last_loan_time = ? WHERE user_id = ?", (new_debt, now, uid))
            
    # Logic lãi ngân hàng (5% mỗi giờ)
    c.execute("SELECT user_id, bank, last_bank_time FROM users WHERE bank > 0")
    savers = c.fetchall()
    for uid, bank, l_time in savers:
        if now - l_time >= 3600: # 1h
            new_bank = int(bank * 1.05)
            c.execute("UPDATE users SET bank = ?, last_bank_time = ? WHERE user_id = ?", (new_bank, now, uid))

    conn.commit()
    conn.close()
@bot.command()

async def taikhoan(ctx):

    u = get_user(ctx.author.id)

    bank_balance = u.get("bank", 0) # Lấy giá trị bank, mặc định là 0 nếu chưa có

   

    await ctx.reply(

        f"🏦 **Hệ Thống Ngân Hàng**\n"

        f"👤 Chủ tài khoản: **{ctx.author.display_name}**\n"

        f"🔒 Số dư đang gửi: **{bank_balance:,} VND**\n"

        f"📈 Lãi suất hiện tại: **5%/giờ**"

    )
# Quản lý sòng bầu cua đang diễn ra
baucua_status = {
    "is_running": False,
    "players": {}, # {user_id: [{"bet": money, "choice": "bau"}]}
    "start_time": 0
}
@bot.command()
async def startbaucua(ctx):
    # SỬA: Dùng 'not in' thay vì '!='
    if ctx.author.id not in ADMIN_IDS:
        return await ctx.reply("❌ Chỉ có **Owner** mới có quyền mở sòng bầu cua!")

    if baucua_status["is_running"]:
        return await ctx.reply("❌ Sòng bầu cua đang mở rồi!")

    # Kích hoạt sòng
    baucua_status["is_running"] = True
    baucua_status["players"] = {}
    
    await ctx.send("# 🎲 **SÒNG BẦU CUA ĐÃ MỞ (BỞI ADMIN)!** 🎲\n⏰ Các con giời có **60 giây** để đặt cược!\n👉 Cú pháp: `!baucua <tiền/all> <bau/cua/tom/ca/ga/nai>`")
    
    await asyncio.sleep(60)
    await finish_baucua(ctx)
@bot.command()
async def baucua(ctx, bet_input: str, choice: str):
    if not baucua_status["is_running"]:
        return await ctx.reply("❌ Sòng chưa mở! Hãy dùng `!startbaucua` để mở sòng trước.")

    u = get_user(ctx.author.id)
    animals = {"bau": "🍐", "cua": "🦀", "tom": "🦐", "ca": "🐟", "ga": "🐔", "nai": "🦌"}
    choice = choice.lower()

    # 1. Kiểm tra lựa chọn và tiền cược
    if choice not in animals:
        return await ctx.reply(f"❌ Chọn sai rồi! Phải chọn: `{', '.join(animals.keys())}`")
    
    if bet_input.lower() == "all":
        bet_money = u["money"]
    else:
        try:
            bet_money = int(bet_input)
        except ValueError:
            return await ctx.reply("❌ Tiền cược phải là số hoặc `all`.")

    if bet_money <= 0 or u["money"] < bet_money:
        return await ctx.reply("❌ Bạn không đủ tiền hoặc tiền cược không hợp lệ!")

    # 2. Lưu thông tin cược (Cho phép cược nhiều lần/nhiều con)
    uid = ctx.author.id
    if uid not in baucua_status["players"]:
        baucua_status["players"][uid] = []
    
    baucua_status["players"][uid].append({
    "bet": bet_money, 
    "choice": choice, 
    "name": ctx.author.display_name  
})
    
    # Trừ tiền ngay khi đặt cược
    update_user(uid, money=u["money"] - bet_money)
    await ctx.reply(f"✅ Đã đặt **{bet_money:,} VND** vào **{animals[choice]} {choice.upper()}**!")

async def finish_baucua(ctx):
    players = baucua_status["players"]
    if not players:
        baucua_status["is_running"] = False
        return await ctx.send("📉 Không có ai đặt cược, sòng bầu cua tự động đóng!")

    animals = {"bau": "🍐", "cua": "🦀", "tom": "🦐", "ca": "🐟", "ga": "🐔", "nai": "🦌"}
    
    # 1. Lắc 3 viên xúc xắc
    results = random.choices(list(animals.keys()), k=3)
    result_text = " ".join([animals[r] for r in results])
    
    # Gửi kết quả xúc xắc trước để đảm bảo người chơi thấy sòng đang lắc
    await ctx.send(f"🎲 **NHÀ CÁI ĐANG LẮC...**\n━━━━━━━━━━━━━━━━━━\n✨ Kết quả: **{result_text}**\n━━━━━━━━━━━━━━━━━━")

    summary = "**💰 TỔNG KẾT TRẢ THƯỞNG:**\n"
    
    # 2. Duyệt danh sách người chơi
    for uid, data in players.items():
        # Lấy thông tin user từ DB
        user = get_user(uid)
        total_win = 0
        
        # Lấy tên từ dữ liệu đã lưu lúc đặt cược (giúp tránh lỗi fetch_user)
        user_name = data[0]["name"] if "name" in data[0] else f"User {uid}"
        
        for b in data:
            match_count = results.count(b["choice"])
            if match_count > 0:
                # Trúng 1 con: x2, 2 con: x3, 3 con: x4 (vốn + thưởng)
                total_win += b["bet"] + (match_count * b["bet"])
        
        if total_win > 0:
            update_user(uid, money=user["money"] + total_win)
            summary += f"✅ **{user_name}**: Nhận **{total_win:,} VND**\n"
        else:
            summary += f"❌ **{user_name}**: Trắng tay!\n"

    # 3. Gửi bảng tổng kết
    await ctx.send(summary)
    
    # 4. Reset trạng thái sòng
    baucua_status["is_running"] = False
    baucua_status["players"] = {}

# ================== NGÂN HÀNG (BANK) ==================
def get_top_debt(limit=10):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    # Chỉ lấy những người có nợ > 0 và sắp xếp từ nợ nhiều nhất xuống ít nhất
    query = """
    SELECT user_id, debt 
    FROM users 
    WHERE debt > 0
    ORDER BY debt DESC 
    LIMIT ?
    """
    c.execute(query, (limit,))
    data = c.fetchall()
    conn.close()
    return data
@bot.command()
async def topno(ctx):
    top_debtors = get_top_debt(10)
    
    if not top_debtors:
        return await ctx.reply("🎉 Thật tuyệt vời! Hiện tại không có ai đang mắc nợ.")

    embed = discord.Embed(
        title="💸 BẢNG XẾP HẠNG NỢ CÔNG",
        color=discord.Color.red() # Màu đỏ cho nó "áp lực"
    )
    
    lines = []
    for i, (uid, debt) in enumerate(top_debtors, 1):
        # Định dạng: số. <@tên_xanh> (Rank): points
        # Chức vụ 'Debtor' có thể thay đổi tùy ý bạn
        lines.append(f"{i}. <@{uid}> (Debtor): **{debt:,} VND**")

    embed.description = "\n".join(lines)
    embed.set_footer(text=f"Trang 1/1 | Yêu cầu bởi: {ctx.author.name}")

    await ctx.send(embed=embed)


def get_top_rich(limit=10):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    # Tính: Tiền mặt + Ngân hàng - Nợ
    query = """
    SELECT user_id, (money + bank - debt) AS total_wealth 
    FROM users 
    ORDER BY total_wealth DESC 
    LIMIT ?
    """
    c.execute(query, (limit,))
    data = c.fetchall()
    conn.close()
    return data
@bot.command()
async def bxhtx(ctx):
    # Lấy dư ra (ví dụ 30 người) để sau khi lọc Admin vẫn còn đủ Top 10 người chơi
    raw_users = get_top_rich(30) 
    
    if not raw_users:
        return await ctx.reply("❌ Hệ thống chưa có dữ liệu xếp hạng!")

    # TÍCH HỢP ẨN ADMIN: Lọc bỏ những ID nằm trong danh sách ADMIN_IDS
    top_users = [user for user in raw_users if user[0] not in ADMIN_IDS][:10]

    embed = discord.Embed(
        title="🏆 Bảng Xếp Hạng Tài Sản",
        color=discord.Color.gold()
    )
    
    description = ""
    for i, (uid, total) in enumerate(top_users, 1):
        # Định dạng theo yêu cầu: Tên (Mention) (Rank): points
        description += f"{i}. <@{uid}> (Member): **{total:,}**\n"

    embed.description = description
    embed.set_footer(text=f"Yêu cầu bởi: {ctx.author.name}")

    await ctx.send(embed=embed)
# ================== Reset database ==================
def reset_all_database():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    # Xóa toàn bộ dữ liệu trong bảng users
    c.execute("DELETE FROM users")
    
    c.execute("UPDATE users SET money = 1000000, debt = 0, bank = 0, loan_today = 0")
    conn.commit()
    conn.close()
@bot.command()
async def resetdb(ctx):
    # Kiểm tra quyền Admin
    if ctx.author.id not in ADMIN_IDS:
        return await ctx.reply("❌ Bạn không có quyền thực hiện lệnh xóa sổ toàn bộ server!")

    # Gửi tin nhắn xác nhận
    await ctx.reply("⚠️ **CẢNH BÁO:** Bạn có chắc chắn muốn RESET TOÀN BỘ database không?\n"
                    "Mọi tài sản, nợ nần sẽ biến mất. Gõ `xacnhan` để đồng ý (Hết hạn sau 15s).")

    def check(m):
        return m.author == ctx.author and m.content == "xacnhan" and m.channel == ctx.channel

    try:
        # Đợi người dùng gõ 'xacnhan' trong 15 giây
        await bot.wait_for("message", check=check, timeout=15.0)
        
        # Thực hiện reset
        reset_all_database()
        
        # Khởi tạo lại bảng mới (đảm bảo DB luôn sạch)
        init_db()
        
        await ctx.send("✅ **RESET THÀNH CÔNG!** Toàn bộ nền kinh tế đã quay về vạch xuất phát.")
        
    except asyncio.TimeoutError:
        await ctx.send("⏳ Đã hết thời gian xác nhận, lệnh reset bị hủy.")

# ================== BÓNG ĐÁ ==================
MATCHES = {
    "1": ["RMD", "BCA"],
    "2": ["MU", "MC"],
    "3": ["LPL", "CLA"]
}
@bot.command()
async def bongda(ctx):
    await ctx.send(
        "**⚽ Các đội hiện đang thi đấu:**\n"
        "Trận 1: Real Madrid (RMD) vs Barca (BCA)\n"
        "Trận 2: Manchester United (MU) vs Manchester City (MC)\n"
        "Trận 3: Liverpool (LPL) vs Chelsea (CLA)\n\n"
        "👉 Dùng `!cuoc <trận> <tiền> <đội>`"
    )

@bot.command()
async def cuoc(ctx, tran: str, money: int, team: str):
    u = get_user(ctx.author.id)
    team = team.upper().strip()
    # 1. Kiểm tra điều kiện (Giữ nguyên logic cũ)
    if tran not in MATCHES:
        return await ctx.reply("❌ Trận không hợp lệ")
    if team not in MATCHES[tran]:
        return await ctx.reply("❌ Đội không thuộc trận này")
    if money <= 0 or money > u["money"]:
        return await ctx.reply(f"❌ Tiền cược không hợp lệ!")

    team1, team2 = MATCHES[tran]
    update_user(ctx.author.id, money=u["money"] - money)

    # 2. Tạo tỷ số và danh sách sự kiện
    score1, score2 = random.randint(0, 4), random.randint(0, 4)
    events = []
    for _ in range(score1): events.append(("goal", random.randint(1, 90), team1))
    for _ in range(score2): events.append(("goal", random.randint(1, 90), team2))
    for _ in range(random.randint(1, 3)): # Thẻ phạt
        events.append((random.choice(["yellow", "red"]), random.randint(1, 90), random.choice([team1, team2])))
    events.sort(key=lambda x: x[1])

    # 3. Khởi tạo tin nhắn gốc và biến lưu lịch sử
    history = "🎙️ Trọng tài đã thổi còi khai cuộc!"
    status_msg = await ctx.reply(
        f"🏟️ **TRỰC TIẾP TRẬN ĐẤU**\n"
        f"⚽ **{team1} 0 - 0 {team2}**\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"{history}"
    )

    curr1, curr2 = 0, 0
    
    # 4. Vòng lặp cập nhật diễn biến (Cộng dồn vào history)
    for etype, minute, data in events:
        await asyncio.sleep(3)

        new_event = ""
        if etype == "goal":
            if data == team1: curr1 += 1
            else: curr2 += 1
            new_event = f"⚽ Phút {minute}: **{data} VÀO!!!** ({curr1}-{curr2})"
        elif etype == "yellow":
            new_event = f"🟨 Phút {minute}: Thẻ vàng cho {data}."
        elif etype == "red":
            new_event = f"🟥 Phút {minute}: Thẻ đỏ cho {data}!"

        # CỘNG DỒN diễn biến mới vào lịch sử (xuống dòng mới)
        history += f"\n{new_event}"

        # Sửa tin nhắn: Cập nhật tỷ số mới và dán toàn bộ lịch sử vào
        await status_msg.edit(content=
            f"🏟️ **TRỰC TIẾP TRẬN ĐẤU**\n"
            f"⚽ **{team1} {curr1} - {curr2} {team2}**\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"{history}"
        )

    # 5. Kết thúc trận đấu
    await asyncio.sleep(2)
    user_now = get_user(ctx.author.id)
    
    # Tính toán thắng thua (Logic công bằng)
    if score1 == score2:
        update_user(ctx.author.id, money=user_now["money"] + money)
        result_text = f"⚖️ **HÒA!** Bạn được hoàn lại **{money:,} VND**."
    else:
        winner = team1 if score1 > score2 else team2
        if team == winner:
            update_user(ctx.author.id, money=user_now["money"] + (money * 2))
            result_text = f"🎉 **THẮNG!** Bạn nhận được **{money * 2:,} VND**."
        else:
            result_text = f"💀 **THUA!** Bạn mất trắng số tiền cược."

    # Lần sửa cuối cùng: Thêm dòng kết quả vào dưới cùng
    await status_msg.edit(content=
        f"🏁 **KẾT THÚC TRẬN ĐẤU**\n"
        f"🏆 Tỷ số cuối cùng: **{team1} {score1} - {score2} {team2}**\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"{history}\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"{result_text}"
    )

PLAYER_ROLE_ID = 1471145734421221589

@bot.before_invoke
async def auto_sync_player(ctx):
    # Đảm bảo người dùng có data trong DB (gọi hàm get_user của bạn)
    # Hàm get_user của bạn sẽ tự tạo data 1000k nếu chưa có
    u = get_user(ctx.author.id)
    
    # Kiểm tra và gắn Role nếu thiếu
    if ctx.guild: # Chỉ chạy trong server, không chạy trong DM
        role = ctx.guild.get_role(PLAYER_ROLE_ID)
        if role and role not in ctx.author.roles:
            try:
                await ctx.author.add_roles(role)
                print(f"✅ Auto-Role: Đã cấp quyền cho {ctx.author.name}")
            except:
                # Thường là do Bot thiếu quyền Manage Roles hoặc Role Bot thấp hơn Role này
                print(f"❌ Auto-Role: Bot thiếu quyền để gắn role cho {ctx.author.name}")

@bot.command()
async def tatbot(ctx):
    # 1. Kiểm tra quyền Admin
    if ctx.author.id not in ADMIN_IDS:
        return await ctx.reply("❌ Bạn không đủ thẩm quyền để đóng sòng!")

    # 2. Xóa tin nhắn lệnh !tatbot ngay lập tức
    try:
        await ctx.message.delete()
    except:
        pass # Tránh lỗi nếu bot thiếu quyền quản lý tin nhắn

    # 3. Gửi thông báo giải tán sòng bài
    await ctx.send(
        "🚨 **BIẾN CĂNG!!!** 🚨\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "👮‍♂️ **Sòng bài đã bị công an hốt, các con nghiện vui lòng đợi chủ sòng quay lại.**\n"
        "🏃‍♂️💨 Giải tán ngay và luôn!\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "💤 *Hệ thống đang ngắt kết nối...*"
    )

    # 4. Ngắt kết nối bot
    print(f"⚠️ Bot đã được tắt từ xa bởi: {ctx.author.name}")
    await bot.close()
# ==================== HỆ THỐNG TIỀN ẢO (CUNG - CẦU) ====================

@tasks.loop(minutes=10)
async def update_market_prices():
    """Biến động giá ngẫu nhiên mỗi 10 phút"""
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT coin_name, current_price FROM market_status")
    rows = c.fetchall()
    for name, price in rows:
        vol = 0.02 # Biến động 2%
        change = random.uniform(-vol, vol)
        new_price = max(0.00000001, price * (1 + change))
        c.execute("UPDATE market_status SET current_price = ? WHERE coin_name = ?", (new_price, name))
    conn.commit()
    conn.close()

@bot.command(name="crypto")
async def crypto_market(ctx):
    """Xem bảng giá sàn giao dịch"""
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT coin_name, current_price, available_supply FROM market_status")
    rows = c.fetchall()
    conn.close()

    embed = discord.Embed(title="📊 SÀN GIAO DỊCH TIỀN ẢO TRỰC TUYẾN", color=0xf1c40f)
    for row in rows:
        name, price, supply = row
        embed.add_field(
            name=f"💰 {name.upper()}", 
            value=f"Giá: **{price:,.4f} $**\nKho: `{supply:,.2f}`", 
            inline=True
        )
    embed.set_footer(text="Giá tăng khi MUA, giảm khi BÁN. Cung - Cầu thực tế!")
    await ctx.send(embed=embed)

@bot.command(name="muacoin")
async def buy_crypto(ctx, coin_name: str, amount: float):
    """Mua coin - Tăng giá thị trường"""
    coin_name = coin_name.lower()
    if amount <= 0: return await ctx.reply("❌ Số lượng phải lớn hơn 0!")

    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT current_price, available_supply FROM market_status WHERE coin_name = ?", (coin_name,))
    market = c.fetchone()
    
    if not market:
        conn.close()
        return await ctx.reply("❌ Coin không tồn tại!")
    
    current_price, available = market
    if amount > available:
        conn.close()
        return await ctx.reply(f"❌ Kho không đủ! Chỉ còn **{available:,.4f}**")

    total_cost = int(current_price * amount)
    u = get_user(ctx.author.id)
    if u['money'] < total_cost:
        conn.close()
        return await ctx.reply(f"❌ Bạn cần **{total_cost:,} VND**")

    # Tính giá mới (Tăng 0.01% cho mỗi đơn vị mua)
    new_price = current_price + (current_price * amount * 0.0001)

    update_user(ctx.author.id, money=u['money'] - total_cost)
    c.execute("UPDATE market_status SET available_supply = available_supply - ?, current_price = ? WHERE coin_name = ?", 
              (amount, new_price, coin_name))
    c.execute("INSERT OR IGNORE INTO crypto (user_id) VALUES (?)", (ctx.author.id,))
    c.execute(f"UPDATE crypto SET {coin_name} = {coin_name} + ? WHERE user_id = ?", (amount, ctx.author.id))
    
    conn.commit()
    conn.close()
    await ctx.reply(f"✅ Đã mua **{amount} {coin_name.upper()}**.\n📈 Giá tăng: **{new_price:,.4f} $**")

@bot.command(name="bancoin")
async def sell_crypto(ctx, coin_name: str, amount: float):
    """Bán coin - Giảm giá thị trường"""
    coin_name = coin_name.lower()
    if amount <= 0: return await ctx.reply("❌ Số lượng phải lớn hơn 0!")

    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute(f"SELECT {coin_name} FROM crypto WHERE user_id = ?", (ctx.author.id,))
    res = c.fetchone()
    if not res or res[0] < amount:
        conn.close()
        return await ctx.reply("❌ Bạn không đủ coin!")

    c.execute("SELECT current_price FROM market_status WHERE coin_name = ?", (coin_name,))
    current_price = c.fetchone()[0]
    total_receive = int(current_price * amount)

    # Tính giá mới (Giảm 0.01% cho mỗi đơn vị bán)
    new_price = max(0.00000001, current_price - (current_price * amount * 0.0001))

    update_user(ctx.author.id, money=get_user(ctx.author.id)['money'] + total_receive)
    c.execute("UPDATE market_status SET available_supply = available_supply + ?, current_price = ? WHERE coin_name = ?", 
              (amount, new_price, coin_name))
    c.execute(f"UPDATE crypto SET {coin_name} = {coin_name} - ? WHERE user_id = ?", (amount, ctx.author.id))
    
    conn.commit()
    conn.close()
    await ctx.reply(f"✅ Đã bán thu về **{total_receive:,} VND**.\n📉 Giá giảm: **{new_price:,.4f} $**")
#================Cấu hình đào coin===================
MINING_COST = 2000000  # Phí đào 2tr
MINING_DURATION = 7200  # 2 tiếng tính bằng giây

@bot.command(name="dao")
async def mine_coin(ctx, coin_name: str):
    coin_name = coin_name.lower()
    if coin_name not in crypto_market_data:
        return await ctx.reply("❌ Loại coin này không tồn tại trong thị trường!")

    current_time = time.time()
    uid = ctx.author.id

    # 1. Kiểm tra xem người dùng có đang đào dở không
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT coin_name, end_time FROM mining WHERE user_id = ?", (uid,))
    mining_job = c.fetchone()

    if mining_job:
        end_time = mining_job[1]
        if current_time < end_time:
            remaining = int(end_time - current_time)
            mins, secs = divmod(remaining, 60)
            hrs, mins = divmod(mins, 60)
            return await ctx.reply(f"⏳ Bạn đang đào {mining_job[0].upper()}. Vui lòng quay lại sau **{hrs}h {mins}m {secs}s**!")
        else:
            # Nếu đã hết thời gian nhưng chưa nhận quà (người dùng gõ !dao lại để check)
            return await claim_mining_reward(ctx, uid, mining_job[0])

    # 2. Kiểm tra tiền mặt để trả phí đào
    u = get_user(uid)
    if u['money'] < MINING_COST:
        return await ctx.reply(f"❌ Bạn cần ít nhất **{MINING_COST:,} VND** để thuê máy đào!")

    # 3. Trừ tiền và bắt đầu đào
    update_user(uid, money=u['money'] - MINING_COST)
    
    finish_at = current_time + MINING_DURATION
    c.execute("INSERT OR REPLACE INTO mining (user_id, coin_name, end_time) VALUES (?, ?, ?)", 
              (uid, coin_name, finish_at))
    conn.commit()
    conn.close()

    await ctx.reply(f"⛏️ Máy đào đã bắt đầu hoạt động! Bạn đã trả **2,000,000 VND** để đào **{coin_name.upper()}**. \nHãy quay lại sau 2 tiếng để nhận kết quả!")

async def claim_mining_reward(ctx, uid, coin_name):
    """Hàm xử lý trao thưởng khi đào xong"""
    # Tính toán số lượng coin ngẫu nhiên dựa trên giá trị (ví dụ từ 500k - 5tr VND)
    min_val = 500000
    max_val = 5000000
    reward_in_vnd = random.randint(min_val, max_val)
    
    coin_price = crypto_market_data[coin_name]["price"]
    amount_earned = round(reward_in_vnd / coin_price, 6)

    # Cập nhật vào bảng crypto
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO crypto (user_id) VALUES (?)", (uid,))
    c.execute(f"UPDATE crypto SET {coin_name} = {coin_name} + ? WHERE user_id = ?", (amount_earned, uid))
    
    # Xóa job đào cũ
    c.execute("DELETE FROM mining WHERE user_id = ?", (uid,))
    conn.commit()
    conn.close()

    embed = discord.Embed(title="✅ ĐÀO COIN HOÀN TẤT", color=0x00ff00)
    embed.description = f"Chúc mừng! Máy đào đã thu hoạch được:\n**{amount_earned:,} {coin_name.upper()}**\n(Trị giá khoảng: ~{reward_in_vnd:,} VND)"
    await ctx.reply(embed=embed)

#===================BLACKJACK=========================
# Định nghĩa màu sắc
COLOR_GRAY = 0x2f3136 # Màu xám tối chuẩn Discord
COLOR_WIN = 0x2ecc71  # Xanh lá
COLOR_LOSE = 0xe74c3c # Đỏ
COLOR_TIE = 0xf1c40f  # Vàng (Hòa)
class BlackjackView(discord.ui.View):
    def __init__(self, ctx, user_bet, bot):
        super().__init__(timeout=60)
        self.ctx = ctx
        self.user_bet = user_bet
        self.bot = bot
        self.deck = [2, 3, 4, 5, 6, 7, 8, 9, 10, 10, 10, 10, 11] * 4
        self.user_hand = [self.draw_card(), self.draw_card()]
        self.dealer_hand = [self.draw_card(), self.draw_card()]

    def draw_card(self):
        return self.deck.pop(random.randint(0, len(self.deck) - 1))

    def get_score(self, hand):
        total = sum(hand)
        aces = hand.count(11)
        while total > 21 and aces > 0:
            total -= 10
            aces -= 1
        return total

    def create_embed(self, title="🃏 BLACKJACK", color=0x2f3136, final=False):
        u_s = self.get_score(self.user_hand)
        d_s = self.get_score(self.dealer_hand)
        embed = discord.Embed(title=title, color=color)
        embed.description = f"**{self.ctx.author.name}**, bạn đã cược **{self.user_bet:,} VND**"
        d_label = f"Dealer [{'?' if not final else d_s}]"
        d_cards = f"🎴 `{self.dealer_hand[0]}` 🎴 `?`" if not final else " ".join([f"🎴 `{c}`" for c in self.dealer_hand])
        embed.add_field(name=d_label, value=d_cards, inline=True)
        u_cards = " ".join([f"🎴 `{c}`" for c in self.user_hand])
        embed.add_field(name=f"{self.ctx.author.name} [{u_s}]", value=u_cards, inline=True)
        return embed

    async def end_game(self, interaction, result):
        for child in self.children: child.disabled = True
        colors = {"win": 0x2ecc71, "lose": 0xe74c3c, "tie": 0xf1c40f}
        titles = {"win": "🎉 THẮNG", "lose": "💀 THUA", "tie": "🤝 HÒA"}
        u = get_user(self.ctx.author.id)
        if result == "win": update_user(self.ctx.author.id, money=u['money'] + self.user_bet)
        elif result == "lose": update_user(self.ctx.author.id, money=u['money'] - self.user_bet)
        await interaction.response.edit_message(embed=self.create_embed(titles[result], colors[result], True), view=self)

    @discord.ui.button(label="Bốc bài", style=discord.ButtonStyle.primary, emoji="👊")
    async def hit(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.ctx.author.id:
            return await interaction.response.send_message("❌ Không phải lượt của ba!", ephemeral=True)
        self.user_hand.append(self.draw_card())
        if self.get_score(self.user_hand) > 21:
            await self.end_game(interaction, "lose")
        else:
            await interaction.response.edit_message(embed=self.create_embed(), view=self)

    @discord.ui.button(label="Dừng", style=discord.ButtonStyle.danger, emoji="🛑")
    async def stand(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.ctx.author.id:
            return await interaction.response.send_message("❌ Không phải lượt của ba!", ephemeral=True)
        while self.get_score(self.dealer_hand) < 17:
            self.dealer_hand.append(self.draw_card())
        u_s, d_s = self.get_score(self.user_hand), self.get_score(self.dealer_hand)
        res = "win" if d_s > 21 or u_s > d_s else "lose" if u_s < d_s else "tie"
        await self.end_game(interaction, res)

    async def end_game(self, interaction, result):
        self.is_finished = True
        for child in self.children:
            child.disabled = True
            
        u_score = self.get_score(self.user_hand)
        d_score = self.get_score(self.dealer_hand)
        u = get_user(self.ctx.author.id)

        if result == "win":
            color = COLOR_WIN
            msg = f"🎉 Bạn đã thắng! +{self.user_bet:,} VND"
            update_user(self.ctx.author.id, money=u['money'] + self.user_bet)
        elif result == "lose":
            color = COLOR_LOSE
            msg = f"💀 Bạn đã thua! -{self.user_bet:,} VND"
            update_user(self.ctx.author.id, money=u['money'] - self.user_bet)
        else:
            color = COLOR_TIE
            msg = "🤝 Hòa! Tiền cược được hoàn trả."

        embed = self.create_embed(title=msg, color=color, final=True)
        await interaction.response.edit_message(embed=embed, view=self)
    @discord.ui.button(label="Bốc bài", style=discord.ButtonStyle.primary, emoji="👊")
    async def hit(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.ctx.author.id:
            return await interaction.response.send_message("❌ Đây không phải lượt của bạn!", ephemeral=True)
        
        self.user_hand.append(self.draw_card())
        score = self.get_score(self.user_hand)
        
        if score > 21:
            await self.end_game(interaction, "lose")
        else:
            # Phải dùng edit_message để cập nhật lại khung xám
            await interaction.response.edit_message(embed=self.create_embed(), view=self)

    @discord.ui.button(label="Dừng", style=discord.ButtonStyle.danger, emoji="🛑")
    async def stand(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.ctx.author.id:
            return await interaction.response.send_message("❌ Đây không phải lượt của bạn!", ephemeral=True)
        
        # Dealer bốc bài
        while self.get_score(self.dealer_hand) < 17:
            self.dealer_hand.append(self.draw_card())
            
        u_score = self.get_score(self.user_hand)
        d_score = self.get_score(self.dealer_hand)
        
        if d_score > 21 or u_score > d_score:
            await self.end_game(interaction, "win")
        elif u_score < d_score:
            await self.end_game(interaction, "lose")
        else:
            await self.end_game(interaction, "tie")

@bot.command(name="blackjack", aliases=["bj"])
async def blackjack(ctx, amount_input: str):
    u = get_user(ctx.author.id)
    
    # Sử dụng logic parse_money để hỗ trợ 1k, 1m, 1b
    def parse_bj_money(val_str, user_money):
        val_str = val_str.lower().strip()
        if val_str == "all": return user_money
        multipliers = {'k': 1000, 'm': 1000000, 'b': 1000000000}
        try:
            for char, multi in multipliers.items():
                if val_str.endswith(char):
                    return int(float(val_str[:-1]) * multi)
            return int(val_str)
        except: return None

    bet = parse_bj_money(amount_input, u['money'])

    if bet is None or bet <= 0:
        return await ctx.reply("❌ Tiền cược không hợp lệ! (Ví dụ: !bj 100k)")
    if u['money'] < bet:
        return await ctx.reply(f"❌ Bạn không đủ tiền! (Có: {u['money']:,} VND)")

    # Khởi tạo view và gửi embed màu xám ban đầu
    view = BlackjackView(ctx, bet, bot)
    
    # Kiểm tra thắng ngay nếu được 21 điểm (Xì dách)
    if view.get_score(view.user_hand) == 21:
        # Tạm thời chưa kết thúc để người chơi bấm, hoặc có thể gọi end_game luôn
        pass

    await ctx.send(embed=view.create_embed(), view=view)

#============================BLACKLIST================================
blacklist_ids = []
@bot.check
async def check_blacklist(ctx):
    return ctx.author.id not in blacklist_ids

@bot.command()
async def blacklist(ctx, user: discord.User):
    if ctx.author.id not in ADMIN_IDS: return
    if user.id not in blacklist_ids:
        blacklist_ids.append(user.id)
        await ctx.reply(f"✅ Đã đưa **{user.name}** vào danh sách đen. Đối tượng này không thể dùng bot!")
    else:
        await ctx.reply("❌ Đối tượng này đã nằm trong danh sách đen rồi.")

@bot.command()
async def unblacklist(ctx, user: discord.User):
    if ctx.author.id not in ADMIN_IDS: return
    if user.id in blacklist_ids:
        blacklist_ids.remove(user.id)
        await ctx.reply(f"✅ Đã gỡ cấm cho **{user.name}**. Đối tượng này đã có thể dùng bot.")
    else:
        await ctx.reply("❌ Đối tượng này không nằm trong danh sách đen.")
 # ==================== BACCARAT ====================
# ==================== BACCARAT IMPROVED ====================
class BaccaratView(discord.ui.View):
    def __init__(self, ctx, user_bet, side):
        super().__init__(timeout=60)
        self.ctx = ctx
        self.user_bet = user_bet
        self.side = side  # 'player', 'banker', hoặc 'tie'
        # Bộ bài rút ngẫu nhiên
        self.deck = [1, 2, 3, 4, 5, 6, 7, 8, 9, 0, 0, 0, 0] * 4 

    def draw_card(self):
        return random.choice(self.deck)

    def calculate_score(self, hand):
        return sum(hand) % 10

    async def play_game(self):
        # Mặc định rút 2 lá cho mỗi bên theo yêu cầu trong ảnh
        p_hand = [self.draw_card(), self.draw_card()]
        b_hand = [self.draw_card(), self.draw_card()]

        p_score = self.calculate_score(p_hand)
        b_score = self.calculate_score(b_hand)

        # Kiểm tra "Đôi" (Hai lá bài giống hệt nhau về giá trị)
        # Theo yêu cầu: Tỉ lệ ra tầm 10%
        has_pair_player = p_hand[0] == p_hand[1]
        has_pair_banker = b_hand[0] == b_hand[1]

        # Xác định kết quả thắng thua
        result = "tie" if p_score == b_score else "player" if p_score > b_score else "banker"
        
        u = get_user(self.ctx.author.id)
        win_amount = 0
        status = "lose"
        bonus_text = ""

        if self.side == result:
            status = "win"
            # Logic thưởng X11 nếu có Đôi bên mình đặt (Tỉ lệ tự nhiên của bộ bài)
            # Nếu muốn ép tỉ lệ 10%, ta kiểm tra thêm random
            if (self.side == "player" and has_pair_player) or (self.side == "banker" and has_pair_banker):
                if random.random() <= 0.10: # Xác suất 10% nổ hũ X11
                    win_amount = self.user_bet * 11
                    bonus_text = "🔥 **JACKPOT ĐÔI: X11 TIỀN THƯỞNG!**"
                else:
                    win_amount = self.user_bet
            elif result == "tie":
                win_amount = self.user_bet * 8 # Hòa ăn 8
            else:
                win_amount = self.user_bet # Thắng bình thường ăn 1
            
            update_user(self.ctx.author.id, money=u['money'] + win_amount)
        else:
            update_user(self.ctx.author.id, money=u['money'] - self.user_bet)

        # Hiển thị kết quả
        color = 0x2ecc71 if status == "win" else 0xe74c3c
        if result == "tie": color = 0xf1c40f
        if bonus_text: color = 0xffd700 # Màu vàng gold cho siêu cấp X11

        embed = discord.Embed(title="🃏 BACCARAT - SÒNG BÀI MAY MẮN", color=color)
        
        p_display = f"Bài: `{' | '.join(map(str, p_hand))}`\nĐiểm: **{p_score}**"
        if has_pair_player: p_display += "\n✨ (Cặp Đôi Player)"
        
        b_display = f"Bài: `{' | '.join(map(str, b_hand))}`\nĐiểm: **{b_score}**"
        if has_pair_banker: b_display += "\n✨ (Cặp Đôi Banker)"

        embed.add_field(name="👤 Player", value=p_display, inline=True)
        embed.add_field(name="🏦 Banker", value=b_display, inline=True)
        
        side_vn = {"player": "PLAYER", "banker": "BANKER", "tie": "HÒA"}
        
        msg = f"🏆 Kết quả: **{side_vn[result]}**\n"
        msg += f"💰 Bạn đặt: **{side_vn[self.side]}**\n"
        msg += f"━━━━━━━━━━━━━━━━━━\n"
        if status == "win":
            msg += f"🎉 **THẮNG:** `+{win_amount:,} VND`\n{bonus_text}"
        else:
            msg += f"💀 **THUA:** `-{self.user_bet:,} VND`"
            
        embed.description = msg
        embed.set_footer(text=f"Số dư hiện tại: {get_user(self.ctx.author.id)['money']:,} VND")
        return embed

@bot.command(name="baccarat", aliases=["bac"])
async def baccarat(ctx, bet_input: str, side: str):
    side = side.lower()
    if side not in ["player", "banker", "tie"]:
        return await ctx.reply("❌ Cửa đặt: `player`, `banker` hoặc `tie`.")

    u = get_user(ctx.author.id)
    
    # Sử dụng hàm parse có sẵn trong code của bạn
    def parse_bac_money(val_str, user_money):
        val_str = val_str.lower().strip()
        if val_str == "all": return user_money
        multipliers = {'k': 1000, 'm': 1000000, 'b': 1000000000}
        try:
            for char, multi in multipliers.items():
                if val_str.endswith(char): return int(float(val_str[:-1]) * multi)
            return int(val_str)
        except: return None

    bet = parse_bac_money(bet_input, u['money'])
    if bet is None or bet <= 0: return await ctx.reply("❌ Tiền cược không hợp lệ!")
    if u['money'] < bet: return await ctx.reply(f"❌ Bạn không đủ tiền! (Ví dụ: `!bac 100k player`) ")

    game = BaccaratView(ctx, bet, side)
    embed = await game.play_game()
    await ctx.reply(embed=embed)

bot.run("MTQ2OTg1MjU0MTUxMTY2Mzg4MA.G01TQF.NZW9zzFafHSxsuSKpo0qssrAELj3qrbXD1G4mA")