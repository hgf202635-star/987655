import os, sys, json, sqlite3, urllib.parse, datetime, base64, re
import aiohttp, uvicorn, discord
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import RedirectResponse, FileResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

CLIENT_ID = "1549657352385597491"
CLIENT_SECRET = "oMasxhmNrgA-vFOMHuHr8DI_ZY3I-sos"
REDIRECT_URI = "http://main-01.oriaxen.com:25591/callback"
SECRET = "AAAAABBBBBCCCCCDDDDDEEEEEFFFFF"
PORT = int(os.environ.get("DASHBOARD_PORT", os.environ.get("SERVER_PORT", "8080")))
API = "https://discord.com/api/v10"
SECTIONS = {"welcome", "tickets", "apply", "rolestatus", "realestate", "activation", "identity", "weapons", "traffic", "aladl", "replies", "bank", "autorole", "embed", "shortcuts", "logs", "security", "badwords", "azkar", "levels", "pointadmin", "setup_points", "iphone"}

# ---------- قاعدة البيانات (يقرأ منها البوت أيضاً) ----------
db = sqlite3.connect("dashboard.db", check_same_thread=False)
db.execute("""CREATE TABLE IF NOT EXISTS settings(
    guild_id TEXT, section TEXT, slot TEXT DEFAULT '', data TEXT,
    PRIMARY KEY(guild_id, section, slot))""")

def get_settings(gid, section, slot=""):
    r = db.execute("SELECT data FROM settings WHERE guild_id=? AND section=? AND slot=?",
                   (str(gid), section, str(slot))).fetchone()
    return json.loads(r[0]) if r else {}

def save_settings(gid, section, data, slot=""):
    db.execute("REPLACE INTO settings VALUES(?,?,?,?)",
               (str(gid), section, str(slot), json.dumps(data, ensure_ascii=False)))
    db.commit()

# ---------- التطبيق ----------
app = FastAPI(docs_url=None, redoc_url=None)
app.add_middleware(SessionMiddleware, secret_key=SECRET, same_site="lax", max_age=86400)

def user_or_401(request: Request):
    u = request.session.get("user")
    if not u:
        raise HTTPException(401, "سجّل دخولك أول")
    return u

def guild_or_403(request: Request, gid: str):
    user_or_401(request)
    if gid not in request.session.get("guilds", []):
        raise HTTPException(403, "ما عندك صلاحية على هذا السيرفر")
    g = request.app.state.bot.get_guild(int(gid))
    if not g:
        raise HTTPException(404, "البوت مو موجود في هذا السيرفر")
    return g

@app.get("/login")
async def login():
    q = urllib.parse.urlencode({"client_id": CLIENT_ID, "redirect_uri": REDIRECT_URI,
                                "response_type": "code", "scope": "identify guilds"})
    return RedirectResponse(f"https://discord.com/oauth2/authorize?{q}")

@app.get("/invite")
async def invite():
    q = urllib.parse.urlencode({"client_id": CLIENT_ID, "permissions": 8, "scope": "bot applications.commands"})
    return RedirectResponse(f"https://discord.com/oauth2/authorize?{q}")

@app.get("/callback")
async def callback(request: Request, code: str):
    async with aiohttp.ClientSession() as s:
        async with s.post(f"{API}/oauth2/token", data={
            "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET, "grant_type": "authorization_code",
            "code": code, "redirect_uri": REDIRECT_URI}) as r:
            if r.status != 200:
                raise HTTPException(400, "فشل تسجيل الدخول")
            token = (await r.json())["access_token"]
        h = {"Authorization": f"Bearer {token}"}
        async with s.get(f"{API}/users/@me", headers=h) as r:
            user = await r.json()
        async with s.get(f"{API}/users/@me/guilds", headers=h) as r:
            guilds = await r.json()
    avatar_hash = user.get("avatar")
    if avatar_hash:
        ext = "gif" if str(avatar_hash).startswith("a_") else "png"
        avatar_url = f"https://cdn.discordapp.com/avatars/{user["id"]}/{avatar_hash}.{ext}?size=256"
    else:
        avatar_url = f"https://cdn.discordapp.com/embed/avatars/{(int(user["id"]) >> 22) % 6}.png"
    request.session["user"] = {
        "id": user["id"],
        "name": user["username"],
        "global_name": user.get("global_name") or user["username"],
        "avatar": avatar_url,
    }
    # نحتفظ بالسيرفرات التي يملكها المستخدم أو لديه فيها صلاحية إدارة السيرفر
    # owner مهم لأن Discord قد لا يعيد OWNER بصلاحية إدارة ضمن permissions في بعض الحالات.
    request.session["guilds"] = [
        g["id"] for g in guilds
        if bool(g.get("owner")) or (int(g.get("permissions", 0)) & (0x8 | 0x20))
    ]
    return RedirectResponse("/")

@app.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/")

@app.get("/api/me")
async def me(request: Request):
    return user_or_401(request)

@app.get("/api/guilds")
async def guilds(request: Request):
    user_or_401(request)
    out = []
    bot = request.app.state.bot
    for gid in request.session.get("guilds", []):
        g = bot.get_guild(int(gid))
        if g:
            out.append({"id": gid, "name": g.name, "icon": g.icon.url if g.icon else None, "bot_present": True})
    return out

@app.get("/api/stats")
async def stats(request: Request):
    user_or_401(request)
    bot = request.app.state.bot
    return {"guilds": len(bot.guilds)}

@app.get("/api/guilds/{gid}/meta")
async def meta(gid: str, request: Request):
    g = guild_or_403(request, gid)
    return {
        "channels": [{"id": str(c.id), "name": c.name} for c in g.text_channels],
        "categories": [{"id": str(c.id), "name": c.name} for c in g.categories],
        "roles": [{"id": str(r.id), "name": r.name} for r in g.roles
                  if not r.is_default() and not r.managed],
    }

# ---------- جسر الربط مع ملفات البوت الحالية ----------
def jload(f):
    try:
        with open(f, encoding="utf-8") as x:
            return json.load(x)
    except Exception:
        return {}

def jsave(f, d):
    with open(f, "w", encoding="utf-8") as x:
        json.dump(d, x, ensure_ascii=False, indent=4)

def sid(v):
    return str(v) if v else ""

def clean(g, d, text, ids, req):
    o = {}
    for k in text:
        v = d.get(k)
        o[k] = (v.strip() if isinstance(v, str) else v) or None
    for k in ids:
        v = d.get(k)
        v = int(v) if str(v or "").isdigit() else None
        if v and not (g.get_channel(v) or g.get_role(v)):
            raise HTTPException(400, "روم أو رتبة ما تتبع هذا السيرفر")
        o[k] = v
    for k in req:
        if not o.get(k):
            raise HTTPException(400, "REQ:" + k)
    return o

def slot_ok(s, n):
    if s not in [str(i) for i in range(1, n + 1)]:
        raise HTTPException(400, "رقم غير صالح")

# التذاكر: tickets_data.json  {guild: {num: {...}}}
TKF = "tickets_data.json"
TK_IDS = ["category_id", "support_role_id", "ticket_mod_role_id", "log_channel_id", "rating_channel_id", "panel_channel_id"]
def r_tk(g, s):
    d = jload(TKF).get(str(g.id), {}).get(s) or {}
    out = {k: (sid(v) if k in TK_IDS else v) for k, v in d.items()}
    out["include_server_image"] = bool(d.get("include_server_image", False))
    return out
def w_tk(g, s, d):
    slot_ok(s, 10)
    o = clean(g, d, ["name", "welcome_msg", "image_url"], TK_IDS,
              ["name", "category_id", "support_role_id", "ticket_mod_role_id", "welcome_msg", "log_channel_id"])
    o["include_server_image"] = bool(d.get("include_server_image", False))
    a = jload(TKF); a.setdefault(str(g.id), {})[s] = o; jsave(TKF, a)

# التقديمات: apply_data.json  {guild: {num: {...}}}
APF = "apply_data.json"
def r_ap(g, s):
    d = jload(APF).get(str(g.id), {}).get(s) or {}
    if not isinstance(d, dict) or not d:
        return {}
    gr = list(d.get("given_roles_ids") or []) + [None, None]
    return {"name": d.get("name"), "admin_role_id": sid(d.get("admin_role_id")),
            "review_channel_id": sid(d.get("review_channel_id")), "accept_msg": d.get("accept_msg"),
            "reject_msg": d.get("reject_msg"), "given1": sid(gr[0]), "given2": sid(gr[1]),
            "removed_role_id": sid(d.get("removed_role_id")), "panel_channel_id": sid(d.get("panel_channel_id")), "questions": d.get("questions") or []}
def w_ap(g, s, d):
    slot_ok(s, 10)
    o = clean(g, d, ["name", "accept_msg", "reject_msg"], ["admin_role_id", "review_channel_id", "removed_role_id", "given1", "given2", "panel_channel_id"],
              ["name", "admin_role_id", "review_channel_id", "accept_msg", "reject_msg"])
    qs = [q.strip() for q in (d.get("questions") or []) if isinstance(q, str) and q.strip()][:15]
    if not qs:
        raise HTTPException(400, "REQ:questions")
    if any(len(q) > 100 for q in qs):
        raise HTTPException(400, "السؤال لازم يكون 100 حرف أو أقل")
    row = {"num": s, "name": o["name"][:90], "admin_role_id": o["admin_role_id"], "review_channel_id": o["review_channel_id"],
           "accept_msg": o["accept_msg"], "reject_msg": o["reject_msg"],
           "given_roles_ids": [x for x in (o["given1"], o["given2"]) if x], "removed_role_id": o["removed_role_id"], "panel_channel_id": o["panel_channel_id"],
           "questions": qs}
    a = jload(APF); a.setdefault(str(g.id), {})[s] = row; jsave(APF, a)

# حالة الرول: roleplay_data.json  {guild: {...}}
RPF = "roleplay_data.json"
RP_IDS = ["panel_channel_id", "channel_id", "ping_role_id", "manager_role_id"]
def r_rp(g, s):
    d = jload(RPF).get(str(g.id), {})
    return {k: (sid(v) if k in RP_IDS else v) for k, v in d.items() if k != "last_status_msg_id"}
def w_rp(g, s, d):
    o = clean(g, d, ["start_msg", "end_msg", "vote_msg"], RP_IDS, ["channel_id"])
    a = jload(RPF); cur = a.get(str(g.id), {}); cur.update(o); a[str(g.id)] = cur; jsave(RPF, a)

# الترحيب والمغادرة: welcome_goodbye.db (النوع 1 = ترحيب، 2 = مغادرة)
WT = {"1": "welcome", "2": "goodbye"}
COLORS = ["أزرق", "أخضر", "أحمر", "أصفر", "بنفسجي", "برتقالي", "أبيض", "أسود"]
EMBEDS = ["ايمبد عادي", "ايمبد حديث"]
BOOLS = ["show_avatar", "ignore_bots", "disable_mention", "status"]
def r_wl(g, s):
    defaults = {"channel_id": "", "color": "أزرق", "show_avatar": True, "ignore_bots": False, "disable_mention": False, "embed_type": "ايمبد عادي", "image_url": "", "embed_text": "", "outside_text": "", "status": True, "avatar_x": 100, "avatar_y": 100, "avatar_size": 150, "text_x": 300, "text_y": 150, "text_size": 40, "text_color": "#FFFFFF"}
    try:
        c = sqlite3.connect("welcome_goodbye.db"); c.row_factory = sqlite3.Row
        c.execute("""CREATE TABLE IF NOT EXISTS welcome_settings (guild_id INTEGER, type TEXT, channel_id INTEGER, color TEXT, show_avatar INTEGER DEFAULT 1, ignore_bots INTEGER DEFAULT 0, disable_mention INTEGER DEFAULT 0, embed_type TEXT, image_url TEXT, embed_text TEXT, outside_text TEXT, status INTEGER DEFAULT 1, avatar_x INTEGER DEFAULT 100, avatar_y INTEGER DEFAULT 100, avatar_size INTEGER DEFAULT 150, text_x INTEGER DEFAULT 300, text_y INTEGER DEFAULT 150, text_size INTEGER DEFAULT 40, text_color TEXT DEFAULT '#FFFFFF', PRIMARY KEY(guild_id,type))""")
        row = c.execute("SELECT * FROM welcome_settings WHERE guild_id=? AND type=?", (g.id, WT.get(s))).fetchone(); c.close()
    except sqlite3.Error: return defaults
    if not row: return defaults
    d = dict(row); d["channel_id"] = sid(d.get("channel_id"))
    if d.get("image_url") and os.path.isfile(str(d["image_url"])): d["image_preview"] = f"/api/guilds/{g.id}/welcome-image?slot={s}"
    else: d["image_preview"] = d.get("image_url", "")
    for k in BOOLS: d[k] = bool(d.get(k))
    for k in ("avatar_x","avatar_y","avatar_size","text_x","text_y","text_size"): d[k] = int(d.get(k) if d.get(k) is not None else defaults[k])
    d["text_color"] = d.get("text_color") or "#FFFFFF"
    return d

def w_wl(g, s, d):
    if s not in WT: raise HTTPException(400, "نوع غير صالح")
    o = clean(g, d, ["color","embed_type","image_url","embed_text","outside_text","text_color"], ["channel_id"], ["channel_id","color","embed_type"])
    if o["color"] not in COLORS or o["embed_type"] not in EMBEDS: raise HTTPException(400, "قيمة غير صالحة")
    image_url = (o.get("image_url") or "").strip()
    image_data = d.get("image_data")
    if image_data:
        if not isinstance(image_data, str) or len(image_data) > 10_000_000: raise HTTPException(400, "حجم الصورة كبير، اختر صورة أقل من 7 ميجابايت")
        match = re.fullmatch(r"data:image/(png|jpeg|jpg|webp);base64,([A-Za-z0-9+/=]+)", image_data)
        if not match: raise HTTPException(400, "صيغة الصورة غير مدعومة، استخدم PNG أو JPG أو WEBP")
        try: raw = base64.b64decode(match.group(2), validate=True)
        except Exception: raise HTTPException(400, "تعذر قراءة الصورة المرفوعة")
        if len(raw) > 7 * 1024 * 1024: raise HTTPException(400, "حجم الصورة يجب أن يكون أقل من 7 ميجابايت")
        ext = "jpg" if match.group(1) in ("jpeg", "jpg") else match.group(1)
        upload_dir = os.path.join(os.getcwd(), "welcome_uploads"); os.makedirs(upload_dir, exist_ok=True)
        image_url = os.path.join(upload_dir, f"{g.id}_{WT[s]}.{ext}")
        with open(image_url, "wb") as image_file: image_file.write(raw)
    elif image_url and not (image_url.startswith(("http://","https://")) or os.path.isfile(image_url)):
        raise HTTPException(400, "ارفع صورة من جهازك أولاً")
    coords = {}
    for key, default, lo, hi in [("avatar_x",100,0,5000),("avatar_y",100,0,5000),("avatar_size",150,20,1000),("text_x",300,0,5000),("text_y",150,0,5000),("text_size",40,8,300)]:
        try: coords[key] = int(d.get(key, default) if str(d.get(key, default)).strip() else default)
        except (ValueError,TypeError): raise HTTPException(400, "إحداثيات صورة الترحيب يجب أن تكون أرقامًا صحيحة")
        if not lo <= coords[key] <= hi: raise HTTPException(400, f"قيمة {key} خارج النطاق المسموح")
    text_color = (o.get("text_color") or "#FFFFFF").strip()
    if not __import__("re").fullmatch(r"#[0-9a-fA-F]{6}", text_color): raise HTTPException(400, "لون النص لازم يكون بصيغة #FFFFFF")
    b = [1 if d.get(k) else 0 for k in BOOLS]
    try:
        c = sqlite3.connect("welcome_goodbye.db")
        c.execute("""CREATE TABLE IF NOT EXISTS welcome_settings (guild_id INTEGER, type TEXT, channel_id INTEGER, color TEXT, show_avatar INTEGER DEFAULT 1, ignore_bots INTEGER DEFAULT 0, disable_mention INTEGER DEFAULT 0, embed_type TEXT, image_url TEXT, embed_text TEXT, outside_text TEXT, status INTEGER DEFAULT 1, avatar_x INTEGER DEFAULT 100, avatar_y INTEGER DEFAULT 100, avatar_size INTEGER DEFAULT 150, text_x INTEGER DEFAULT 300, text_y INTEGER DEFAULT 150, text_size INTEGER DEFAULT 40, text_color TEXT DEFAULT '#FFFFFF', PRIMARY KEY(guild_id,type))""")
        existing = {row[1] for row in c.execute("PRAGMA table_info(welcome_settings)").fetchall()}
        additions = {"status":"INTEGER DEFAULT 1","avatar_x":"INTEGER DEFAULT 100","avatar_y":"INTEGER DEFAULT 100","avatar_size":"INTEGER DEFAULT 150","text_x":"INTEGER DEFAULT 300","text_y":"INTEGER DEFAULT 150","text_size":"INTEGER DEFAULT 40","text_color":"TEXT DEFAULT '#FFFFFF'"}
        for col, definition in additions.items():
            if col not in existing: c.execute(f"ALTER TABLE welcome_settings ADD COLUMN {col} {definition}")
        c.execute("""INSERT INTO welcome_settings (guild_id,type,channel_id,color,show_avatar,ignore_bots,disable_mention,embed_type,image_url,embed_text,outside_text,status,avatar_x,avatar_y,avatar_size,text_x,text_y,text_size,text_color) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(guild_id,type) DO UPDATE SET channel_id=excluded.channel_id,color=excluded.color,show_avatar=excluded.show_avatar,ignore_bots=excluded.ignore_bots,disable_mention=excluded.disable_mention,embed_type=excluded.embed_type,image_url=excluded.image_url,embed_text=excluded.embed_text,outside_text=excluded.outside_text,status=excluded.status,avatar_x=excluded.avatar_x,avatar_y=excluded.avatar_y,avatar_size=excluded.avatar_size,text_x=excluded.text_x,text_y=excluded.text_y,text_size=excluded.text_size,text_color=excluded.text_color""",
            (g.id,WT[s],o["channel_id"],o["color"],b[0],b[1],b[2],o["embed_type"],image_url,o["embed_text"],o["outside_text"],b[3],coords["avatar_x"],coords["avatar_y"],coords["avatar_size"],coords["text_x"],coords["text_y"],coords["text_size"],text_color))
        c.commit(); c.close()
    except sqlite3.Error: raise HTTPException(500, "قاعدة بيانات الترحيب غير جاهزة")

# الهوية: id_data.json  {guild: {...}} (يحافظ على قائمة من قدّموا سابقاً)
IDF = "id_data.json"
ID_IDS = ["admins_channel_id", "add_role_id", "rem_role_id", "panel_channel_id"]
ID_TEXT = ["panel_title", "panel_description", "image_url"]
def r_id(g, s):
    d = jload(IDF)
    if not isinstance(d, dict):
        return {}
    o = {k: sid(d.get(k)) for k in ID_IDS}
    o.update({k: d.get(k, "") or "" for k in ID_TEXT})
    o["change_nick"] = bool(d.get("change_nick", True))
    o["include_server_image"] = bool(d.get("include_server_image", True))
    return o
def w_id(g, s, d):
    o = clean(g, d, ID_TEXT, ID_IDS, ["admins_channel_id"])
    # id.py uses one top-level config; preserve submitted users in its native format.
    a = jload(IDF)
    if not isinstance(a, dict):
        a = {}
    submitted = a.get("submitted", [])
    a.update(o)
    a["submitted"] = submitted if isinstance(submitted, list) else []
    a["change_nick"] = bool(d.get("change_nick", True))
    a["include_server_image"] = bool(d.get("include_server_image", True))
    jsave(IDF, a)

# جداول إعدادات بسيطة (صف لكل سيرفر)
def simple(db, table, cols):
    def r(g, s):
        try:
            c = sqlite3.connect(db)
            x = c.execute(f"SELECT {','.join(cols)} FROM {table} WHERE guild_id=?", (g.id,)).fetchone(); c.close()
        except sqlite3.Error:
            return {}
        return {k: sid(v) for k, v in zip(cols, x)} if x else {}
    def w(g, s, d):
        o = clean(g, d, [], cols, cols)
        try:
            c = sqlite3.connect(db)
            c.execute(f"INSERT OR REPLACE INTO {table}(guild_id,{','.join(cols)}) VALUES(?{',?' * len(cols)})", (g.id, *[o[k] for k in cols]))
            c.commit(); c.close()
        except sqlite3.Error:
            raise HTTPException(500, "قاعدة البيانات غير جاهزة")
    return r, w

# ---------- العقارات: real_estate.db (جدول settings: key, channel_id) ----------
REDB = "real_estate.db"
RE_KEYS = ["citizen_channel", "admin_channel", "requests_channel", "market_channel", "logs_channel"]
def r_re(g, s):
    try:
        c = sqlite3.connect(REDB)
        rows = dict(c.execute("SELECT key, channel_id FROM settings").fetchall()); c.close()
    except sqlite3.Error:
        return {}
    return {k: sid(rows.get(k)) for k in RE_KEYS if rows.get(k)}
def w_re(g, s, d):
    o = clean(g, d, [], RE_KEYS, ["requests_channel", "market_channel", "logs_channel"])
    try:
        c = sqlite3.connect(REDB)
        c.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, channel_id INTEGER)")
        for k in RE_KEYS:
            if o[k]:
                c.execute("INSERT OR REPLACE INTO settings (key, channel_id) VALUES (?, ?)", (k, o[k]))
            else:
                c.execute("DELETE FROM settings WHERE key=?", (k,))
        c.commit(); c.close()
    except sqlite3.Error:
        raise HTTPException(500, "قاعدة بيانات العقارات غير جاهزة")

# الأذكار: azkar_settings.db (نفس قاعدة azkhar.py)
AZKDB = "azkar_settings.db"
def r_azkar(g, s):
    try:
        c = sqlite3.connect(AZKDB, timeout=10); c.row_factory = sqlite3.Row
        r = c.execute("SELECT channel_id, interval_minutes, status FROM server_azkar WHERE guild_id=?", (g.id,)).fetchone(); c.close()
    except sqlite3.Error:
        return {}
    if not r:
        return {"channel_id": "", "interval": 15, "status": False}
    return {"channel_id": sid(r["channel_id"]), "interval": int(r["interval_minutes"] or 15), "status": str(r["status"]).upper() == "TRUE"}

def w_azkar(g, s, d):
    try:
        interval = int(d.get("interval") or 15)
    except (TypeError, ValueError):
        raise HTTPException(400, "المدة غير صالحة")
    if interval not in (5, 10, 15, 30, 45):
        raise HTTPException(400, "اختر مدة من 5 أو 10 أو 15 أو 30 أو 45 دقيقة")
    enabled = bool(d.get("status"))
    raw = str(d.get("channel_id") or "")
    channel_id = int(raw) if raw.isdigit() else 0
    if channel_id and not g.get_channel(channel_id):
        raise HTTPException(400, "روم الأذكار ما يتبع هذا السيرفر")
    if enabled and not channel_id:
        raise HTTPException(400, "حدد روم الأذكار عند تفعيل النظام")
    try:
        c = sqlite3.connect(AZKDB, timeout=10)
        c.execute("""CREATE TABLE IF NOT EXISTS server_azkar (
            guild_id INTEGER PRIMARY KEY, channel_id INTEGER,
            interval_minutes INTEGER DEFAULT 15, status TEXT DEFAULT 'FALSE'
        )""")
        c.execute("""INSERT INTO server_azkar(guild_id, channel_id, interval_minutes, status)
            VALUES(?,?,?,?)
            ON CONFLICT(guild_id) DO UPDATE SET channel_id=excluded.channel_id,
            interval_minutes=excluded.interval_minutes, status=excluded.status""",
            (g.id, channel_id, interval, "TRUE" if enabled else "FALSE"))
        c.commit(); c.close()
    except sqlite3.Error:
        raise HTTPException(500, "قاعدة بيانات الأذكار غير جاهزة")

# المستويات: levels_settings.db (نفس قاعدة levels.py)
LVDB = "levels_settings.db"
LEVEL_DEFAULT = "مبروك {user}، لقد صعدت إلى المستوى {level}! (المستوى القادم: {next-level})"
def r_levels(g, s):
    try:
        c = sqlite3.connect(LVDB, timeout=10); c.row_factory = sqlite3.Row
        r = c.execute("SELECT channel_id, custom_message FROM server_levels_settings WHERE guild_id=?", (g.id,)).fetchone(); c.close()
    except sqlite3.Error:
        return {}
    if not r:
        return {"channel_id": "", "message": LEVEL_DEFAULT}
    return {"channel_id": sid(r["channel_id"]), "message": r["custom_message"] or LEVEL_DEFAULT}

def w_levels(g, s, d):
    channel_id = str(d.get("channel_id") or "")
    if not channel_id.isdigit() or not g.get_channel(int(channel_id)):
        raise HTTPException(400, "حدد روم نصي صالح للمستويات")
    message = str(d.get("message") or "").strip()
    if not message:
        raise HTTPException(400, "اكتب رسالة المستويات")
    if len(message) > 2000:
        raise HTTPException(400, "رسالة المستويات 2000 حرف كحد أقصى")
    allowed = {"{user}", "{level}", "{next-level}", "{xp}"}
    import re
    fields = set(re.findall(r"\{[^{}]+\}", message))
    unknown = fields - allowed
    if unknown:
        raise HTTPException(400, "متغير غير معروف: " + ", ".join(sorted(unknown)))
    try:
        c = sqlite3.connect(LVDB, timeout=10)
        c.execute("""CREATE TABLE IF NOT EXISTS server_levels_settings (
            guild_id INTEGER PRIMARY KEY, channel_id INTEGER,
            custom_message TEXT
        )""")
        c.execute("""INSERT INTO server_levels_settings(guild_id, channel_id, custom_message)
            VALUES(?,?,?) ON CONFLICT(guild_id) DO UPDATE SET channel_id=excluded.channel_id, custom_message=excluded.custom_message""",
            (g.id, int(channel_id), message))
        c.commit(); c.close()
    except sqlite3.Error:
        try: c.close()
        except Exception: pass
        raise HTTPException(500, "قاعدة بيانات المستويات غير جاهزة")

# إعدادات الأسلحة والمرور الخاصة بمرسل البانلات
def r_wpn(g, s):
    return AD["weapons"][0](g, s) if "AD" in globals() and "weapons" in AD else {}

def r_traffic(g, s):
    return AD["traffic"][0](g, s) if "AD" in globals() and "traffic" in AD else {}

# ---------- إرسال البانلات بعد الحفظ ----------
db.execute("""CREATE TABLE IF NOT EXISTS panels(
    guild_id TEXT, section TEXT, slot TEXT, channel_id TEXT, message_id TEXT,
    PRIMARY KEY(guild_id, section, slot))""")

async def replace_panel(g, section, slot, ch, **kw):
    """يحذف البانل القديمة (إن وجدت) ويرسل الجديدة، عشان ما تتكرر مع كل حفظ."""
    r = db.execute("SELECT channel_id, message_id FROM panels WHERE guild_id=? AND section=? AND slot=?",
                   (str(g.id), section, slot)).fetchone()
    if r:
        try:
            old = g.get_channel(int(r[0]))
            await (await old.fetch_message(int(r[1]))).delete()
        except Exception:
            pass
    msg = await ch.send(**kw)
    db.execute("REPLACE INTO panels VALUES(?,?,?,?,?)", (str(g.id), section, slot, str(ch.id), str(msg.id)))
    db.commit()

async def panel_re(bot, g, slot):
    m = find_mod(bot, "AdminPanelView", "نظام العقارات")
    d = r_re(g, "")
    items = [
        ("admin_channel", "admin", m.AdminPanelView, discord.Embed(
            title="🏢 إدارة العقارات والمحلات",
            description="لوحة تحكم المسؤولين\n\nمن هنا تقدر تدير جميع العقارات والمحلات وأرباحها والطلبات.",
            color=discord.Color.from_rgb(43, 45, 49))),
        ("citizen_channel", "citizen", m.CitizenPanelView, discord.Embed(
            title="🏡 سوق العقارات والمحلات والممتلكات",
            description="اختر القسم المطلوب للاستعراض أو الشراء، أو استعرض ممتلكاتك، أو استلم أرباح محلاتك من القائمة أدناه:",
            color=discord.Color.green())),
    ]
    n = 0
    for key, name, view, emb in items:
        ch = g.get_channel(int(d[key])) if d.get(key) else None
        if ch:
            await replace_panel(g, "realestate", name, ch, embed=emb, view=view()); n += 1
    return n

# الأقسام الأخرى تسجّل دالتها من الكوج: bot.dashboard_panels["tickets"] = async def f(bot, g, slot)
def find_mod(bot, attr, name):
    for c in bot.cogs.values():
        m = sys.modules.get(type(c).__module__)
        if m and hasattr(m, attr):
            return m
    # Fallback: extension module may be loaded under a different module name.
    for module_name, m in list(sys.modules.items()):
        if m and (module_name == "bank" or module_name.endswith(".bank")) and hasattr(m, attr):
            return m
    raise RuntimeError(name + " غير محمّل في البوت أو أن ملفه لا يحتوي على " + attr)

# التفعيل: activation_settings.json (يقرأه كوج apply_2 لكل سيرفر)
ACF = "activation_settings.json"
AC_IDS = ["panel_channel_id", "staff_channel_id", "add_role_id", "remove_role_id", "staff_role_id"]
AC_TEXT = ["panel_title", "panel_description", "panel_image_url"]
def r_ac(g, s):
    d = jload(ACF).get(str(g.id)) or {}
    out = {k: sid(d.get(k)) for k in AC_IDS if d.get(k)}
    out.update({k: d.get(k, "") or "" for k in AC_TEXT})
    out["include_server_image"] = bool(d.get("include_server_image", True))
    return out
def w_ac(g, s, d):
    o = clean(g, d, AC_TEXT, AC_IDS, ["staff_channel_id"])
    o["include_server_image"] = bool(d.get("include_server_image", True))
    a = jload(ACF); old = a.get(str(g.id), {}) if isinstance(a, dict) else {}
    old.update(o); a[str(g.id)] = old; jsave(ACF, a)

async def panel_ac(bot, g, slot):
    m = find_mod(bot, "build_panel_embed", "نظام التفعيل")
    d = r_ac(g, "")
    ch = g.get_channel(int(d["panel_channel_id"])) if d.get("panel_channel_id") else None
    if not ch:
        return 0
    embed = m.build_panel_embed(g, title=d.get("panel_title") or None,
        description=d.get("panel_description") or None,
        image_url=d.get("panel_image_url") or None,
        include_server_image=d.get("include_server_image", True))
    await replace_panel(g, "activation", "", ch, embed=embed, view=m.ApplyPanelView())
    return 1

# الردود التلقائية: auto_responses.json  {guild: {keyword: {response, match_type, mention}}}
ARF = "auto_responses.json"
def ar_legacy(a):
    return any(isinstance(v, dict) and "response" in v and "match_type" in v for v in a.values())
def r_ar(g, s):
    a = jload(ARF)
    if ar_legacy(a):
        return {"items": [], "legacy": True}
    return {"items": [{"keyword": k, "response": v.get("response", ""), "match_type": v.get("match_type", "contains"),
                       "mention": v.get("mention") == "true"} for k, v in (a.get(str(g.id)) or {}).items()]}
def w_ar(g, s, d):
    a = jload(ARF)
    if ar_legacy(a):
        raise HTTPException(409, "حدّث ملف الردود reply.py بالنسخة الجديدة أول")
    new = {}
    for it in (d.get("items") or []):
        if not isinstance(it, dict):
            continue
        k = str(it.get("keyword") or "").lower().strip(); r = str(it.get("response") or "").strip()
        if not k and not r:
            continue
        if not k or not r:
            raise HTTPException(400, "كل رد لازم له كلمة مفتاحية ونص")
        if len(k) > 100 or len(r) > 2000:
            raise HTTPException(400, "الكلمة 100 حرف والرد 2000 حرف كحد أقصى")
        new[k] = {"response": r, "match_type": "exact" if it.get("match_type") == "exact" else "contains",
                  "mention": "true" if it.get("mention") else "false"}
    a[str(g.id)] = new; jsave(ARF, a)

# البنك: bank.db (جدول guild_settings لكل سيرفر)
BKDB = "bank.db"
BK_IDS = ["account_panel_channel_id", "citizens_panel_channel_id", "staff_panel_channel_id",
          "loan_channel_id", "log_channel_id", "bank_staff_role_id"]
BK_TEXT = ["account_title", "account_description", "account_image", "account_icon",
           "citizens_title", "citizens_description", "citizens_image", "citizens_icon",
           "staff_title", "staff_description", "staff_image", "staff_icon"]
def bk_cols(c):
    for k in BK_IDS:
        try: c.execute(f"ALTER TABLE guild_settings ADD COLUMN {k} INTEGER")
        except sqlite3.OperationalError: pass
    for k in BK_TEXT:
        try: c.execute(f"ALTER TABLE guild_settings ADD COLUMN {k} TEXT")
        except sqlite3.OperationalError: pass
def r_bk(g, s):
    try:
        c = sqlite3.connect(BKDB, timeout=10); c.row_factory = sqlite3.Row
        bk_cols(c)
        r = c.execute(f"SELECT {','.join(BK_IDS + BK_TEXT)} FROM guild_settings WHERE guild_id=?", (g.id,)).fetchone(); c.close()
    except sqlite3.Error:
        return {}
    return ({k: sid(r[k]) for k in BK_IDS if r and r[k]} |
            {k: (r[k] if r and r[k] is not None else "") for k in BK_TEXT})
def w_bk(g, s, d):
    o = clean(g, d, [], BK_IDS, [])
    txt = {k: str(d.get(k, "") or "")[:4000] for k in BK_TEXT}
    for k in ("account_icon", "citizens_icon", "staff_icon"):
        txt[k] = "1" if str(d.get(k, "1")).lower() in ("1","true","on","yes") else "0"
    try:
        c = sqlite3.connect(BKDB, timeout=10)
        c.execute("CREATE TABLE IF NOT EXISTS guild_settings (guild_id INTEGER PRIMARY KEY, loan_channel_id INTEGER, log_channel_id INTEGER, bank_staff_role_id INTEGER)")
        bk_cols(c)
        c.execute("INSERT OR IGNORE INTO guild_settings(guild_id) VALUES(?)", (g.id,))
        values = {**o, **txt}
        cols = BK_IDS + BK_TEXT
        c.execute(f"UPDATE guild_settings SET {','.join(k + '=?' for k in cols)} WHERE guild_id=?", (*[values.get(k) for k in cols], g.id))
        c.commit(); c.close()
    except sqlite3.Error:
        raise HTTPException(500, "قاعدة بيانات البنك غير جاهزة")

async def panel_bk(bot, g, slot):
    # البنك يعرّف الـ Views ودالة make_custom_embed داخل bank.py
    m = find_mod(bot, "CreateAccountView", "نظام البنك")
    d = r_bk(g, "")
    items = [
        ("account_panel_channel_id", "account", "🏦 إنشاء حساب بنكي", "اضغط الزر بالأسفل لإنشاء حسابك البنكي.", "CreateAccountView"),
        ("citizens_panel_channel_id", "citizens", "🏦 خدمات المواطنين", "اختر الخدمة البنكية المناسبة من القائمة بالأسفل.", "CitizenServicesView"),
        ("staff_panel_channel_id", "staff", "🏦 لوحة مسؤولي البنك", "أدوات إدارة الحسابات والخدمات البنكية للمسؤولين.", "BankStaffView"),
    ]
    n = 0
    for key, name, default_title, default_desc, view_name in items:
        cid = str(d.get(key) or "")
        ch = g.get_channel(int(cid)) if cid.isdigit() else None
        if not isinstance(ch, discord.TextChannel):
            continue
        title = str(d.get(f"{name}_title") or default_title)[:256]
        desc = str(d.get(f"{name}_description") or default_desc)[:4000]
        image = str(d.get(f"{name}_image") or "").strip()
        show_icon = str(d.get(f"{name}_icon", "true")).lower() in ("true", "1")
        embed = m.make_custom_embed(g, title, desc, image_url=image or None, show_icon=show_icon)
        view_cls = getattr(m, view_name, None)
        if view_cls is None:
            raise RuntimeError(f"فئة البانل {view_name} غير موجودة داخل bank.py")
        await replace_panel(g, "bank", name, ch, embed=embed, view=view_cls())
        n += 1
    if n == 0:
        raise HTTPException(400, "حدد رومًا واحدًا على الأقل من إعدادات بانلات البنك")
    return n

async def panel_role_status(bot, g, slot):
    m = find_mod(bot, "RPView", "نظام حالة الرول")
    d = r_rp(g, "")
    cid = str(d.get("panel_channel_id") or "")
    ch = g.get_channel(int(cid)) if cid.isdigit() else None
    if not ch:
        raise HTTPException(400, "حدد روم إرسال بانل حالة الرول")
    emb = discord.Embed(
        title="🎮 لوحة تحكم إدارة الرول بلاي",
        description="اختر الحالة المناسبة من القائمة المنسدلة بالأسفل لتحديث حالة الرول مباشرة في الروم المخصص.",
        color=discord.Color.dark_embed()
    )
    await replace_panel(g, "rolestatus", "main", ch, embed=emb, view=m.RPView(bot))
    return 1

async def panel_identity(bot, g, slot):
    m = find_mod(bot, "IDSelectView", "نظام الهوية")
    d = r_id(g, "")
    cid = str(d.get("panel_channel_id") or "")
    ch = g.get_channel(int(cid)) if cid.isdigit() else None
    if not ch:
        raise HTTPException(400, "حدد روم إرسال بانل الهوية")
    emb = discord.Embed(
        title=str(d.get("panel_title") or "الهوية الوطنية")[:256],
        description=str(d.get("panel_description") or "اختر من القائمة أدناه للبدء في عملية تقديم طلب الهوية الوطنية.")[:4000],
        color=discord.Color.blue()
    )
    if str(d.get("include_server_image", True)).lower() in ("true", "1", "on", "yes") and g.icon:
        emb.set_thumbnail(url=g.icon.url)
    if d.get("image_url"):
        emb.set_image(url=str(d["image_url"]))
    await replace_panel(g, "identity", "main", ch, embed=emb, view=m.IDSelectView(bot))
    return 1

async def panel_weapons(bot, g, slot):
    m = find_mod(bot, "WeaponPanelView", "نظام الأسلحة")
    d = r_wpn(g, "")
    cid = str(d.get("panel_channel") or "")
    ch = g.get_channel(int(cid)) if cid.isdigit() else None
    if not ch:
        raise HTTPException(400, "حدد روم إرسال بانل الأسلحة")
    issue_price = int(d.get("issue_price") or 31000)
    renew_price = int(d.get("renew_price") or 2500)
    title = str(d.get("embed_title") or "🔫 مكتب إصدار وتجديد تصاريح الأسلحة الرسمية")[:256]
    description = str(d.get("embed_description") or (
        "للحصول على تصريح سلاح جديد أو تجديد تصريحك الحالي، أو استعراض تفاصيل "
        "تصريحك، يرجى استخدام القائمة أدناه.\n*(ملاحظة: مسموح لك بتصريح نشط واحد فقط)*\n\n"
        f"💰 **رسوم الإصدار الجديد:** `{issue_price:,}` ريال\n"
        f"🔄 **رسوم التجديد:** `{renew_price:,}` ريال\n⏳ **مدة الصلاحية:** 10 أيام لكل تفعيل"
    ))[:4000]
    emb = discord.Embed(title=title, description=description, color=discord.Color.dark_red())
    if d.get("embed_image_url"):
        emb.set_image(url=str(d["embed_image_url"]))
    if str(d.get("show_guild_icon", "false")).lower() in ("true", "1") and g.icon:
        emb.set_thumbnail(url=g.icon.url)
    await replace_panel(g, "weapons", "main", ch, embed=emb, view=m.WeaponPanelView())
    return 1

async def panel_traffic(bot, g, slot):
    m = find_mod(bot, "TrafficControlView", "نظام المرور")
    cog = next((c for c in bot.cogs.values() if type(c).__module__ == m.__name__), None)
    if cog is None:
        raise HTTPException(500, "نظام المرور غير محمّل في البوت")
    d = r_traffic(g, "")
    sent = 0

    control = g.get_channel(int(d["control_channel"])) if d.get("control_channel") else None
    if control:
        emb = discord.Embed(
            title=str(d.get("control_title") or "🛡️ لوحة تحكم العساكر ورجال المرور")[:256],
            description=str(d.get("control_description") or (
                "مرحباً بك في نظام إدارة المرور العسكري\n\n"
                "• **تسجيل مخالفة:** لتعبئة بيانات المركبة واختيار العضو والمخالفة (تسحب تلقائياً خلال 5 أيام إذا لم تُسدد).\n"
                "• **إزالة مخالفة:** لاختيار عضو وإزالة مخالفة خاطئة (تعرض المخالفات النشطة فقط) مع إرسال إشعار ولُوق.\n"
                "• **عرض المخالفات الغير مسدده:** لعرض قائمة الأشخاص الذين عليهم مخالفات ومعاينة تفاصيل كل شخص بدقة."
            ))[:4000],
            color=discord.Color.dark_red()
        )
        if d.get("control_image"): emb.set_image(url=str(d["control_image"]))
        if str(d.get("control_icon", "false")).lower() in ("true", "1") and g.icon: emb.set_thumbnail(url=g.icon.url)
        await replace_panel(g, "traffic", "control", control, embed=emb, view=m.TrafficControlView(cog))
        sent += 1

    citizen = g.get_channel(int(d["citizen_channel"])) if d.get("citizen_channel") else None
    if citizen:
        emb = discord.Embed(
            title=str(d.get("citizen_title") or "🚦 خدمات المرور العامة للمواطنين")[:256],
            description=str(d.get("citizen_description") or (
                "مرحباً بك في البانل الرسمي لخدمات المرور\n\n"
                "• **مخالفاتي:** للاستعلام عن مخالفاتك غير المسددة فقط.\n"
                "• **سداد مخالفة:** لاختيار وسداد الغرامات آلياً عبر قائمة منسدلة."
            ))[:4000],
            color=discord.Color.blue()
        )
        if d.get("citizen_image"): emb.set_image(url=str(d["citizen_image"]))
        if str(d.get("citizen_icon", "false")).lower() in ("true", "1") and g.icon: emb.set_thumbnail(url=g.icon.url)
        await replace_panel(g, "traffic", "citizen", citizen, embed=emb, view=m.TrafficCitizenView(cog))
        sent += 1

    if not sent:
        raise HTTPException(400, "حدد روم بانل العساكر أو روم بانل المواطنين")
    return sent

# ---------- وزارة العدل: إعدادات من لوحة التحكم ----------
ALADL_FILE = "aladl_data.json"
ADL_CHANNEL_IDS = ["target_channel_id", "panel_channel_id", "citizens_panel_channel_id", "lawyer_channel_id", "lawyer_panel_channel_id"]
ADL_ROLE_IDS = ["responsible_role_id", "lawyer_role_id"]
ADL_TEXT = ["panel_title", "panel_description", "panel_image_url", "citizens_title", "citizens_description", "citizens_image_url", "case_image_url", "lawyer_panel_title", "lawyer_panel_description", "lawyer_panel_image_url"]
def r_adl(g, s):
    try:
        with open(ALADL_FILE, "r", encoding="utf-8") as f: data = json.load(f)
        cfg = data.get("settings", {}).get(str(g.id), {})
        return {**{k: sid(cfg.get(k)) for k in ADL_CHANNEL_IDS + ADL_ROLE_IDS},
                **{k: cfg.get(k, "") or "" for k in ADL_TEXT},
                "show_guild_icon": bool(cfg.get("show_guild_icon", True)),
                "lawyer_show_icon": bool(cfg.get("lawyer_show_icon", True))}
    except (OSError, ValueError, TypeError):
        return {}
def w_adl(g, s, d):
    ids = {}
    for k in ADL_CHANNEL_IDS + ADL_ROLE_IDS:
        v = d.get(k)
        v = int(v) if str(v or "").isdigit() else None
        if v and not (g.get_channel(v) or g.get_role(v)):
            raise HTTPException(400, "روم أو رتبة وزارة العدل غير صالحة")
        ids[k] = v
    for k in ("panel_channel_id", "citizens_panel_channel_id", "lawyer_panel_channel_id", "lawyer_channel_id", "target_channel_id"):
        if ids[k] and not isinstance(g.get_channel(ids[k]), discord.TextChannel):
            raise HTTPException(400, "روم وزارة العدل غير صالح")
    for k in ("responsible_role_id", "lawyer_role_id"):
        if ids[k] and not g.get_role(ids[k]):
            raise HTTPException(400, "رتبة وزارة العدل غير صالحة")
    try:
        try:
            with open(ALADL_FILE, "r", encoding="utf-8") as f: data = json.load(f)
        except (OSError, ValueError): data = {"settings": {}, "cases": [], "lawyer_requests": {}}
        cfg = data.setdefault("settings", {}).setdefault(str(g.id), {})
        for k, v in ids.items():
            if v: cfg[k] = v
            else: cfg.pop(k, None)
        for k in ADL_TEXT:
            if k in d: cfg[k] = str(d.get(k) or "")[:4000]
        if "show_guild_icon" in d: cfg["show_guild_icon"] = bool(d.get("show_guild_icon"))
        if "lawyer_show_icon" in d: cfg["lawyer_show_icon"] = bool(d.get("lawyer_show_icon"))
        with open(ALADL_FILE, "w", encoding="utf-8") as f: json.dump(data, f, ensure_ascii=False, indent=4)
    except OSError:
        raise HTTPException(500, "تعذر حفظ إعدادات وزارة العدل")

async def panel_adl(bot, g, slot):
    cog = bot.get_cog("AladlCog")
    if not cog:
        raise HTTPException(400, "نظام وزارة العدل غير محمّل في البوت")
    m = find_mod(bot, "build_custom_embed", "وزارة العدل")
    cfg = r_adl(g, slot)
    sent = 0
    panels = [
        ("panel_channel_id", "cases", cfg.get("panel_title") or "⚖️ نظام وزارة العدل - رفع القضايا",
         cfg.get("panel_description") or "اضغط على الزر أدناه لتقديم شكوى أو رفع قضية جديدة رسمياً.",
         cfg.get("panel_image_url"), cfg.get("show_guild_icon", True), m.RaiseCaseView(), "وزارة العدل © جميع الحقوق محفوظة", discord.Color.gold()),
        ("citizens_panel_channel_id", "citizens", cfg.get("citizens_title") or "👤 سجلات وملفات المواطنين",
         cfg.get("citizens_description") or "اضغط على الزر أدناه للاستعلام عن سجل أي مواطن والقضايا الخاصة به.",
         cfg.get("citizens_image_url"), cfg.get("show_guild_icon", True), m.CitizensPanelView(), "وزارة العدل © نظام السجلات", discord.Color.dark_blue()),
        ("lawyer_panel_channel_id", "lawyer", cfg.get("lawyer_panel_title") or "🧑‍⚖️ طلب محامي",
         cfg.get("lawyer_panel_description") or "اضغط على الزر أدناه لتقديم طلب محامي، وسيتم إشعارك بالنتيجة في الخاص.",
         cfg.get("lawyer_panel_image_url"), cfg.get("lawyer_show_icon", True), m.LawyerRequestPanelView(), "وزارة العدل © طلبات المحامين", discord.Color.gold()),
    ]
    for key, name, title, description, image_url, show_icon, view, footer, color in panels:
        channel_id = cfg.get(key)
        channel = g.get_channel(int(channel_id)) if str(channel_id or "").isdigit() else None
        if not isinstance(channel, discord.TextChannel):
            continue
        embed = m.build_custom_embed(g, title=title, description=description, color=color,
                                     image_url=image_url or None, show_icon=show_icon)
        embed.set_footer(text=footer)
        await replace_panel(g, "aladl", name, channel, embed=embed, view=view)
        sent += 1
    if not sent:
        raise HTTPException(400, "حدد روم بانل واحد على الأقل: رفع القضايا أو المواطنين أو المحامين")
    return sent

# ---------- نقاط الإدارة ونظام تسطيب النقاط (مطابق لواجهات point.py و setup_point.py) ----------
POINT_COLORS = {
    "blue": discord.Color.blue(), "red": discord.Color.red(),
    "green": discord.Color.green(), "gold": discord.Color.gold(),
    "orange": discord.Color.orange(), "purple": discord.Color.purple(),
    "dark_blue": discord.Color.dark_blue(), "dark_red": discord.Color.dark_red(),
}

def _point_module(name):
    import importlib
    try:
        return sys.modules.get(name) or importlib.import_module(name)
    except Exception:
        return None

def _point_embed(g, d):
    title = str(d.get("title") or "نظام النقاط")[:256]
    desc = str(d.get("description") or "اختر من القائمة لإدارة النقاط")[:4000]
    color = POINT_COLORS.get(str(d.get("color") or "blue"), discord.Color.blue())
    embed = discord.Embed(title=title, description=desc, color=color)
    image = str(d.get("image_url") or "").strip()
    if image:
        if not image.startswith(("https://", "http://")):
            raise HTTPException(400, "رابط الصورة لازم يبدأ بـ http:// أو https://")
        embed.set_image(url=image)
    if str(d.get("show_server_icon") or "false").lower() == "true" and g.icon:
        embed.set_thumbnail(url=g.icon.url)
    embed.set_footer(text=g.name, icon_url=g.icon.url if g.icon else None)
    return embed

async def panel_pointadmin(bot, g, slot):
    d = get_settings(g.id, "pointadmin", slot)
    channel_id = int(d.get("channel_id") or 0)
    channel = g.get_channel(channel_id) if channel_id else None
    if not isinstance(channel, discord.TextChannel):
        raise HTTPException(400, "حدد روم إرسال بانل نقاط الإدارة أولاً")
    module = _point_module("point")
    if not module or not hasattr(module, "MemberPointsView") or not hasattr(module, "AdminControlView"):
        raise HTTPException(400, "ملف point.py غير محمّل في البوت؛ حمّل الإضافة باسم point أولاً")
    action = d.get("panel_type", "member")
    if action == "admin":
        role_id = int(d.get("role_id") or 0)
        log_id = int(d.get("log_channel_id") or 0)
        role = g.get_role(role_id) if role_id else None
        log_channel = g.get_channel(log_id) if log_id else None
        if not role:
            raise HTTPException(400, "اختر الرتبة المسؤولة عن التحكم بالنقاط")
        if not isinstance(log_channel, discord.TextChannel):
            raise HTTPException(400, "اختر روم لوق النقاط")
        module.server_configs[g.id] = {"role_id": role.id, "log_channel_id": log_channel.id}
        view = module.AdminControlView(g.id)
    else:
        view = module.MemberPointsView()
    await channel.send(embed=_point_embed(g, d), view=view)
    return True

async def panel_setup_points(bot, g, slot):
    d = get_settings(g.id, "setup_points", slot)
    channel_id = int(d.get("channel_id") or 0)
    channel = g.get_channel(channel_id) if channel_id else None
    if not isinstance(channel, discord.TextChannel):
        raise HTTPException(400, "حدد روم إرسال بانل نظام النقاط أولاً")
    module = _point_module("setup_point")
    if not module or not hasattr(module, "MemberPointsView") or not hasattr(module, "AdminControlView"):
        raise HTTPException(400, "ملف setup_point.py غير محمّل في البوت؛ حمّل الإضافة باسم setup_point أولاً")
    try:
        panel_id = int(d.get("panel_id") or 1)
    except (TypeError, ValueError):
        raise HTTPException(400, "اختر رقم بانل من 1 إلى 10")
    if panel_id < 1 or panel_id > 10:
        raise HTTPException(400, "رقم البانل لازم يكون بين 1 و10")
    action = d.get("panel_type", "admin")
    if action == "member":
        view = module.MemberPointsView(g.id, panel_id)
    else:
        system_name = str(d.get("system_name") or "إدارة").strip()[:80]
        role_id = int(d.get("role_id") or 0)
        log_id = int(d.get("log_channel_id") or 0)
        role = g.get_role(role_id) if role_id else None
        log_channel = g.get_channel(log_id) if log_id else None
        if not role:
            raise HTTPException(400, "اختر الرتبة المسؤولة عن التحكم بهذا النظام")
        if not isinstance(log_channel, discord.TextChannel):
            raise HTTPException(400, "اختر روم لوق النقاط")
        module.server_configs[(g.id, panel_id, system_name)] = {"role_id": role.id, "log_channel_id": log_channel.id}
        systems_key = (g.id, panel_id)
        if systems_key not in module.panel_systems:
            module.panel_systems[systems_key] = []
        if system_name not in module.panel_systems[systems_key]:
            module.panel_systems[systems_key].append(system_name)
        view = module.AdminControlView(g.id, panel_id, system_name)
    await channel.send(embed=_point_embed(g, d), view=view)
    return True

PANEL_SENDERS = {
    "realestate": panel_re,
    "activation": panel_ac,
    "bank": panel_bk,
    "rolestatus": panel_role_status,
    "identity": panel_identity,
    "weapons": panel_weapons,
    "traffic": panel_traffic,
    "aladl": panel_adl,
    "pointadmin": panel_pointadmin,
    "setup_points": panel_setup_points,
    "iphone": lambda bot, g, slot: panel_iphone(bot, g, slot),
}
PANEL_KEYS = {
    "realestate": ["admin_channel", "citizen_channel"],
    "activation": ["panel_channel_id"],
    "rolestatus": ["panel_channel_id"],
    "identity": ["panel_channel_id"],
    "weapons": ["panel_channel"],
    "traffic": ["control_channel", "citizen_channel"],
    "aladl": ["panel_channel_id", "citizens_panel_channel_id", "lawyer_panel_channel_id"],
    "bank": ["account_panel_channel_id", "citizens_panel_channel_id", "staff_panel_channel_id"],
    "pointadmin": ["channel_id"],
    "setup_points": ["channel_id"],
    "iphone": ["panel_channel_id"],
}

async def send_panel(bot, g, section, slot):
    fn = PANEL_SENDERS.get(section) or getattr(bot, "dashboard_panels", {}).get(section)
    if not fn:
        return "unsupported"
    try:
        result = await fn(bot, g, slot)
        return "sent" if result else "none"
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, "تعذر إرسال بانل %s: %s" % (section, str(e)[:120]))

# الرتبة التلقائية: autorole.db  جدول auto_roles(guild_id PK, role_id)
ARDB = "autorole.db"
def r_au(g, s):
    try:
        c = sqlite3.connect(ARDB, timeout=10)
        r = c.execute("SELECT role_id FROM auto_roles WHERE guild_id=?", (g.id,)).fetchone(); c.close()
    except sqlite3.Error:
        return {}
    return {"role_id": sid(r[0])} if r and r[0] else {}
def w_au(g, s, d):
    rid = clean(g, d, [], ["role_id"], [])["role_id"]
    if rid:
        role = g.get_role(rid)
        if not role or role.is_default() or role.managed:
            raise HTTPException(400, "هذي الرتبة ما تنفع كرتبة تلقائية")
        me = getattr(g, "me", None)
        if me and role.position >= me.top_role.position:
            raise HTTPException(400, "الرتبة أعلى من رتبة البوت أو تساويها، ارفع رتبة البوت فوقها أول")
    try:
        c = sqlite3.connect(ARDB, timeout=10)
        c.execute("CREATE TABLE IF NOT EXISTS auto_roles (guild_id INTEGER PRIMARY KEY, role_id INTEGER)")
        if rid:
            c.execute("INSERT INTO auto_roles (guild_id, role_id) VALUES (?, ?) ON CONFLICT(guild_id) DO UPDATE SET role_id = excluded.role_id", (g.id, rid))
        else:
            c.execute("DELETE FROM auto_roles WHERE guild_id=?", (g.id,))
        c.commit(); c.close()
    except sqlite3.Error:
        raise HTTPException(500, "قاعدة بيانات الرتبة التلقائية غير جاهزة")

# إرسال ايمبد نصي (نفس أمر /send-embed): يرسل مباشرة ولا يحفظ إلا آخر قيم للتسهيل
EMB_COLORS = {"أزرق": discord.Color.blue, "أخضر": discord.Color.green, "أحمر": discord.Color.red,
              "أصفر": discord.Color.gold, "بنفسجي": discord.Color.purple, "برتقالي": discord.Color.orange,
              "أبيض": discord.Color.light_embed, "أسود": discord.Color.dark_theme}

async def send_custom_embed(request, g, d):
    uid = int(request.session["user"]["id"])
    try:
        m = g.get_member(uid) or await g.fetch_member(uid)
        is_admin = m.guild_permissions.administrator
    except Exception:
        is_admin = False
    if not is_admin:
        raise HTTPException(403, "إرسال الايمبد للأدمن فقط")
    cid = str(d.get("channel_id") or "")
    ch = g.get_channel(int(cid)) if cid.isdigit() else None
    if not ch or str(getattr(ch, "type", "")) != "text":
        raise HTTPException(400, "REQ:channel_id")
    title, desc = (d.get("title") or "").strip(), (d.get("description") or "").strip()
    if not title:
        raise HTTPException(400, "REQ:title")
    if not desc:
        raise HTTPException(400, "REQ:description")
    if len(title) > 256 or len(desc) > 4000:
        raise HTTPException(400, "العنوان 256 حرف والوصف 4000 حرف كحد أقصى")
    image = (d.get("image_url") or "").strip()
    if image and not image.startswith(("http://", "https://")):
        raise HTTPException(400, "رابط الصورة لازم يبدأ بـ http أو https")
    outside = (d.get("outside_text") or "").strip() or None
    if outside and len(outside) > 2000:
        raise HTTPException(400, "النص الخارجي 2000 حرف كحد أقصى")
    emb = discord.Embed(title=title, description=desc, timestamp=discord.utils.utcnow(),
                        color=EMB_COLORS.get(d.get("color"), discord.Color.blue)())
    if image:
        emb.set_image(url=image)
    try:
        await ch.send(content=outside, embed=emb)
    except Exception:
        raise HTTPException(500, "تعذر الإرسال، تأكد من صلاحيات البوت في الروم")
    save_settings(str(g.id), "embed", {k: d.get(k) for k in ("channel_id", "color", "title", "description", "image_url", "outside_text")})

# الاختصارات: shortcuts.db  جدول server_shortcuts(guild_id, shortcut_name, target_command, status)
SCDB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "shortcuts.db")
SC_ON, SC_OFF = "مفعّل", "معطل"  # مفعّل / معطل

def ensure_shortcuts_table(c):
    """إنشاء الجدول وترقية قواعد البيانات القديمة دون حذف البيانات."""
    c.execute("""CREATE TABLE IF NOT EXISTS server_shortcuts (
        guild_id INTEGER, shortcut_name TEXT, target_command TEXT,
        status TEXT DEFAULT 'مفعّل', options TEXT DEFAULT '{}',
        PRIMARY KEY (guild_id, shortcut_name))""")
    cols = {row[1] for row in c.execute("PRAGMA table_info(server_shortcuts)").fetchall()}
    if "status" not in cols:
        c.execute("ALTER TABLE server_shortcuts ADD COLUMN status TEXT DEFAULT 'مفعّل'")
    if "options" not in cols:
        c.execute("ALTER TABLE server_shortcuts ADD COLUMN options TEXT DEFAULT '{}'")
    # إصلاح حالة التفعيل التي حفظتها نسخة قديمة كنص م... حرفي، وليس ككلمة عربية.
    c.execute("UPDATE server_shortcuts SET status=? WHERE status=?", (SC_ON, r"\u0645\u0641\u0639\u0651\u0644"))
    c.execute("UPDATE server_shortcuts SET status=? WHERE status=?", (SC_OFF, r"\u0645\u0639\u0637\u0644"))
    c.commit()

def r_sc(g, s):
    try:
        c = sqlite3.connect(SCDB, timeout=20)
        ensure_shortcuts_table(c)
        rows = c.execute("SELECT shortcut_name, target_command, status, options FROM server_shortcuts WHERE guild_id=? ORDER BY shortcut_name", (g.id,)).fetchall()
        c.close()
    except sqlite3.Error:
        return {"items": []}
    items = []
    for a, b, c_, opts in rows:
        try: options = json.loads(opts or "{}") if isinstance(opts, str) else {}
        except Exception: options = {}
        # توافق مع الاختصارات القديمة: إعطاء/سحب الرتبة صار اختصاراً واحداً.
        if b in ("add_role", "remove_role"):
            b = "toggle_role"
        items.append({"name": a, "target": b, "enabled": c_ == SC_ON, "options": options})
    return {"items": items}
def w_sc(g, s, d):
    items, seen = [], set()
    for it in (d.get("items") or []):
        if not isinstance(it, dict):
            continue
        name = str(it.get("name") or "").strip().lower().lstrip("!/.#")
        target = str(it.get("target") or "").strip().lstrip("/")
        if not name and not target:
            continue
        allowed = {"ban", "kick", "timeout", "untimeout", "clear", "warn", "unban", "toggle_role", "nickname"}
        if not name or not target:
            raise HTTPException(400, "كل اختصار لازم له اسم ونوع أمر")
        if target not in allowed:
            raise HTTPException(400, "نوع الأمر غير مدعوم")
        if any(ch.isspace() for ch in name):
            raise HTTPException(400, "الاختصار لازم يكون كلمة وحدة بدون مسافات: " + name)
        if len(name) > 50 or len(target) > 100:
            raise HTTPException(400, "الاختصار 50 حرف والأمر 100 حرف كحد أقصى")
        if name in seen:
            raise HTTPException(400, "الاختصار مكرر: " + name)
        seen.add(name)
        opts = it.get("options") if isinstance(it.get("options"), dict) else {}
        # الاختصار يحدد نوع العملية فقط؛ الأرقام والمنشنات والرتب تُكتب داخل ديسكورد.
        # لا توجد إعدادات ثابتة للعدد أو الرتبة من الموقع.
        opts = {"reason_required": bool(opts.get("reason_required"))}
        items.append((g.id, name, target, SC_ON if it.get("enabled") else SC_OFF, json.dumps(opts, ensure_ascii=False)))
    try:
        c = sqlite3.connect(SCDB, timeout=20)
        ensure_shortcuts_table(c)
        with c:
            c.execute("DELETE FROM server_shortcuts WHERE guild_id=?", (g.id,))
            c.executemany("INSERT INTO server_shortcuts (guild_id, shortcut_name, target_command, status, options) VALUES (?,?,?,?,?)", items)
        c.close()
    except sqlite3.Error as e:
        try:
            c.close()
        except Exception:
            pass
        print(f"[shortcuts] database error: {e}")
        raise HTTPException(500, "تعذر حفظ الاختصارات في قاعدة البيانات؛ راجع سجل تشغيل الموقع")


# اللوقات: logs_settings.db  جدول server_logs(guild_id, log_type, channel_id)  (حذف الصف = إيقاف هذا اللوق)
LGDB = "logs_settings.db"
LG_TYPES = ["members_join", "members_remove", "member_bans", "member_kick", "member_timeout", "member_roles", "messages",
            "reactions", "channels_crud", "server_updates", "voice_logs"]
def r_lg(g, s):
    try:
        c = sqlite3.connect(LGDB, timeout=10)
        rows = c.execute("SELECT log_type, channel_id FROM server_logs WHERE guild_id=?", (g.id,)).fetchall(); c.close()
    except sqlite3.Error:
        return {}
    return {t: sid(ch) for t, ch in rows if t in LG_TYPES and ch}
def w_lg(g, s, d):
    vals = {}
    for t in LG_TYPES:
        v = str(d.get(t) or "")
        if not v:
            vals[t] = None
            continue
        ch = g.get_channel(int(v)) if v.isdigit() else None
        if not ch or str(getattr(ch, "type", "")) != "text":
            raise HTTPException(400, "اللوق لازم يكون في روم نصي من هذا السيرفر")
        vals[t] = ch.id
    try:
        c = sqlite3.connect(LGDB, timeout=10)
        with c:
            c.execute("CREATE TABLE IF NOT EXISTS server_logs (guild_id INTEGER, log_type TEXT, channel_id INTEGER, PRIMARY KEY (guild_id, log_type))")
            for t, cid in vals.items():
                if cid:
                    c.execute("INSERT INTO server_logs (guild_id, log_type, channel_id) VALUES (?,?,?) ON CONFLICT(guild_id, log_type) DO UPDATE SET channel_id=excluded.channel_id", (g.id, t, cid))
                else:
                    c.execute("DELETE FROM server_logs WHERE guild_id=? AND log_type=?", (g.id, t))
        c.close()
    except sqlite3.Error:
        raise HTTPException(500, "قاعدة بيانات اللوقات غير جاهزة")

# أدوات الحماية: security_settings.db  جدولي server_security(status) و security_punishments(punishment)
SEDB = "security_settings.db"
SE_FEATS = ["bot_add", "admin_role", "mass_ban", "mass_kick", "channel_delete", "role_delete", "role_create", "channel_create", "links"]
SE_PUN = ["timeout", "kick", "ban", "strip_roles"]
def r_se(g, s):
    try:
        c = sqlite3.connect(SEDB, timeout=10)
        st = dict(c.execute("SELECT feature, status FROM server_security WHERE guild_id=?", (g.id,)).fetchall())
        pu = dict(c.execute("SELECT feature, punishment FROM security_punishments WHERE guild_id=?", (g.id,)).fetchall())
        try:
            ignored = c.execute("SELECT role_id FROM security_ignored_roles WHERE guild_id=? ORDER BY role_id LIMIT 1", (g.id,)).fetchone()
            ignored_role_id = str(ignored[0]) if ignored else ""
        except sqlite3.Error:
            ignored_role_id = ""
        c.close()
    except sqlite3.Error:
        st, pu, ignored_role_id = {}, {}, ""
    o = {"ignored_role_id": ignored_role_id}
    for f in SE_FEATS:
        o[f + "__on"] = bool(st.get(f, 0))
        o[f + "__p"] = pu.get(f, "timeout")
    return o
def w_se(g, s, d):
    rows = []
    for f in SE_FEATS:
        p = d.get(f + "__p") or "timeout"
        if p not in SE_PUN:
            raise HTTPException(400, "عقوبة غير صالحة")
        rows.append((f, 1 if d.get(f + "__on") else 0, p))
    try:
        c = sqlite3.connect(SEDB, timeout=10)
        with c:
            c.execute("CREATE TABLE IF NOT EXISTS server_security (guild_id INTEGER, feature TEXT, status INTEGER DEFAULT 0, PRIMARY KEY (guild_id, feature))")
            c.execute("CREATE TABLE IF NOT EXISTS security_punishments (guild_id INTEGER, feature TEXT, punishment TEXT DEFAULT 'timeout', PRIMARY KEY (guild_id, feature))")
            c.execute("CREATE TABLE IF NOT EXISTS security_ignored_roles (guild_id INTEGER PRIMARY KEY, role_id INTEGER)")
            c.execute("DELETE FROM security_ignored_roles WHERE guild_id=?", (g.id,))
            ignored_role = str(d.get("ignored_role_id") or "").strip()
            if ignored_role:
                if not ignored_role.isdigit() or not g.get_role(int(ignored_role)):
                    raise HTTPException(400, "الرتبة المستثناة غير صحيحة")
                c.execute("INSERT INTO security_ignored_roles (guild_id, role_id) VALUES (?,?)", (g.id, int(ignored_role)))
            for f, on, p in rows:
                c.execute("INSERT INTO server_security (guild_id, feature, status) VALUES (?,?,?) ON CONFLICT(guild_id, feature) DO UPDATE SET status=excluded.status", (g.id, f, on))
                c.execute("INSERT INTO security_punishments (guild_id, feature, punishment) VALUES (?,?,?) ON CONFLICT(guild_id, feature) DO UPDATE SET punishment=excluded.punishment", (g.id, f, p))
        c.close()
    except sqlite3.Error:
        raise HTTPException(500, "قاعدة بيانات الحماية غير جاهزة")

ADMIN_ONLY = {"security", "badwords", "azkar", "levels", "pointadmin", "setup_points"}  # أقسام التعديل فيها للأدمن فقط (مثل أوامر البوت)
async def require_admin(request, g):
    try:
        uid = int(request.session["user"]["id"])
        m = g.get_member(uid) or await g.fetch_member(uid)
        ok = m.guild_permissions.administrator
    except Exception:
        ok = False
    if not ok:
        raise HTTPException(403, "تعديل هذا القسم للأدمن فقط")

# الكلمات المحظورة: badwords_settings.db  جدولي bad_words و bad_words_punishment
BWDB = "badwords_settings.db"
def r_bw(g, s):
    try:
        c = sqlite3.connect(BWDB, timeout=10)
        words = [r[0] for r in c.execute("SELECT word FROM bad_words WHERE guild_id=? ORDER BY word", (g.id,)).fetchall()]
        p = c.execute("SELECT punishment FROM bad_words_punishment WHERE guild_id=?", (g.id,)).fetchone()
        c.close()
    except sqlite3.Error:
        words, p = [], None
    return {"words": words, "punishment": p[0] if p else "timeout"}
def w_bw(g, s, d):
    pun = d.get("punishment") or "timeout"
    if pun not in SE_PUN:
        raise HTTPException(400, "عقوبة غير صالحة")
    words, seen = [], set()
    for x in d.get("words") or []:
        x = str(x).strip().lower()
        if not x:
            continue
        if len(x) < 2:
            raise HTTPException(400, "الكلمة لازم تكون حرفين على الأقل: " + x)
        if len(x) > 100:
            raise HTTPException(400, "الكلمة أطول من 100 حرف")
        if x not in seen:
            seen.add(x); words.append(x)
    if len(words) > 500:
        raise HTTPException(400, "الحد الأقصى 500 كلمة")
    try:
        c = sqlite3.connect(BWDB, timeout=10)
        with c:
            c.execute("CREATE TABLE IF NOT EXISTS bad_words (guild_id INTEGER, word TEXT, PRIMARY KEY (guild_id, word))")
            c.execute("CREATE TABLE IF NOT EXISTS bad_words_punishment (guild_id INTEGER PRIMARY KEY, punishment TEXT DEFAULT 'timeout')")
            c.execute("DELETE FROM bad_words WHERE guild_id=?", (g.id,))
            c.executemany("INSERT INTO bad_words (guild_id, word) VALUES (?,?)", [(g.id, x) for x in words])
            c.execute("INSERT INTO bad_words_punishment (guild_id, punishment) VALUES (?,?) ON CONFLICT(guild_id) DO UPDATE SET punishment=excluded.punishment", (g.id, pun))
        c.close()
    except sqlite3.Error:
        raise HTTPException(500, "قاعدة بيانات الكلمات المحظورة غير جاهزة")

def module_settings(dbfile, table, id_cols, extra_cols):
    """Read/write the real module SQLite settings table, adding optional config columns when needed."""
    all_cols = id_cols + extra_cols
    def ensure(c):
        # Tables are created by the bot cogs; this only adds the columns exposed in the dashboard.
        for name in extra_cols:
            typ = "INTEGER" if name in ("show_guild_icon","control_icon","citizen_icon","issue_price","renew_price") else "TEXT"
            try: c.execute(f"ALTER TABLE {table} ADD COLUMN {name} {typ}")
            except sqlite3.OperationalError: pass
    def read(g, s):
        try:
            c = sqlite3.connect(dbfile, timeout=10); c.row_factory = sqlite3.Row; ensure(c)
            row = c.execute(f"SELECT {','.join(all_cols)} FROM {table} WHERE guild_id=?", (g.id,)).fetchone(); c.close()
            if not row: return {}
            return {k: (sid(row[k]) if k in id_cols else (bool(row[k]) if k in ("show_guild_icon","control_icon","citizen_icon") else row[k])) for k in all_cols if row[k] is not None}
        except sqlite3.Error: return {}
    def write(g, s, data):
        ids = clean(g, data, [], id_cols, [])
        vals = dict(ids)
        for k in extra_cols:
            v = data.get(k)
            if k in ("show_guild_icon","control_icon","citizen_icon"): vals[k] = int(str(v).lower() in ("true","1","on","yes"))
            elif k in ("issue_price","renew_price"):
                try: vals[k] = max(0, min(int(v), 1000000000)) if v not in (None,"") else (31000 if k=="issue_price" else 2500)
                except (ValueError,TypeError): raise HTTPException(400, "الرسوم يجب أن تكون أرقامًا صحيحة")
            else: vals[k] = str(v or "")[:4000]
        try:
            c=sqlite3.connect(dbfile, timeout=10); ensure(c)
            c.execute(f"INSERT OR IGNORE INTO {table}(guild_id) VALUES(?)",(g.id,))
            c.execute(f"UPDATE {table} SET {','.join(k+'=?' for k in all_cols)} WHERE guild_id=?",(*[vals.get(k) for k in all_cols],g.id))
            c.commit(); c.close()
        except sqlite3.Error as e: raise HTTPException(500, "تعذر حفظ إعدادات النظام")
    return read, write

# نظام iPhone: يستخدم نفس ملف الإعدادات الذي يقرأه iphone.py
def r_iphone(g, s):
    try:
        cog = getattr(app.state.bot, "get_cog", lambda _n: None)("IPhoneCog")
        module = sys.modules.get(cog.__class__.__module__) if cog else None
        path = getattr(module, "SETTINGS_FILE", os.path.join(os.path.dirname(os.path.abspath(__file__)), "iphone_settings.json"))
        with open(path, "r", encoding="utf-8") as f:
            all_cfg = json.load(f)
        cfg = all_cfg.get(str(g.id), {})
        return {k: (bool(v) if k == "include_server_image" else (str(v) if v is not None else "")) for k, v in cfg.items()}
    except (OSError, ValueError, TypeError):
        return {}

def w_iphone(g, s, data):
    cog = getattr(app.state.bot, "get_cog", lambda _n: None)("IPhoneCog")
    module = sys.modules.get(cog.__class__.__module__) if cog else None
    path = getattr(module, "SETTINGS_FILE", os.path.join(os.path.dirname(os.path.abspath(__file__)), "iphone_settings.json"))
    try:
        try:
            with open(path, "r", encoding="utf-8") as f: all_cfg = json.load(f)
        except (OSError, ValueError): all_cfg = {}
        old = all_cfg.get(str(g.id), {})
        def channel_id(key):
            value = data.get(key, old.get(key))
            if value in (None, ""): return None
            try:
                ident = int(value)
            except (TypeError, ValueError): raise HTTPException(400, "اختر رومًا صحيحًا")
            if key.endswith("channel_id") and not g.get_channel(ident):
                raise HTTPException(400, "أحد الرومات المحددة غير موجود في السيرفر")
            return ident
        panel = channel_id("panel_channel_id")
        if panel is None: raise HTTPException(400, "اختر روم إرسال بانل الهاتف")
        tweet_channel = channel_id("tweet_channel_id")
        role_value = data.get("tweet_role_id", old.get("tweet_role_id"))
        try: tweet_role = int(role_value) if role_value not in (None, "") else None
        except (TypeError, ValueError): raise HTTPException(400, "اختر رتبة صحيحة للتغريدات")
        if tweet_role is not None and not g.get_role(tweet_role): raise HTTPException(400, "رتبة التغريدات غير موجودة في السيرفر")
        cfg = {**old, "panel_channel_id": panel, "tweet_channel_id": tweet_channel, "tweet_role_id": tweet_role,
               "title": str(data.get("title", old.get("title", "")) or "")[:256],
               "description": str(data.get("description", old.get("description", "")) or "")[:4000],
               "image_url": str(data.get("image_url", old.get("image_url", "")) or "")[:2000],
               "include_server_image": str(data.get("include_server_image", old.get("include_server_image", True))).lower() in ("true", "1", "on", "yes")}
        if cfg["image_url"] and not cfg["image_url"].startswith(("https://", "http://")):
            raise HTTPException(400, "رابط صورة البانل لازم يبدأ بـ http أو https")
        all_cfg[str(g.id)] = cfg
        with open(path, "w", encoding="utf-8") as f: json.dump(all_cfg, f, ensure_ascii=False, indent=4)
    except HTTPException: raise
    except OSError as e: raise HTTPException(500, "تعذر حفظ إعدادات iPhone: " + str(e)[:100])

async def panel_iphone(bot, g, slot):
    cog = bot.get_cog("IPhoneCog")
    if not cog: raise HTTPException(400, "حمّل ملف iphone.py في البوت أولاً")
    cfg = r_iphone(g, slot)
    channel = g.get_channel(int(cfg.get("panel_channel_id") or 0))
    if not isinstance(channel, discord.TextChannel): raise HTTPException(400, "اختر روم إرسال بانل الهاتف")
    module = sys.modules.get(cog.__class__.__module__)
    view_cls = getattr(module, "PhoneView", None) if module else None
    if view_cls is None: raise HTTPException(500, "تعذر العثور على PhoneView داخل iphone.py")
    embed = discord.Embed(title=cfg.get("title") or "نظام الجوال",
        description=cfg.get("description") or "اختر الخدمة المطلوبة من القائمة المنسدلة أدناه للوصول إلى خدمات الهاتف.",
        color=discord.Color.blue())
    if str(cfg.get("include_server_image", "true")).lower() in ("true", "1") and g.icon:
        embed.set_thumbnail(url=g.icon.url)
    image_url = cfg.get("image_url") or getattr(cog, "DEFAULT_EMBED_IMAGE", "")
    if image_url: embed.set_image(url=image_url)
    await channel.send(embed=embed, view=view_cls())
    return True

AD = {"badwords": (r_bw, w_bw), "security": (r_se, w_se), "logs": (r_lg, w_lg), "shortcuts": (r_sc, w_sc), "iphone": (r_iphone, w_iphone), "autorole": (r_au, w_au), "realestate": (r_re, w_re), "activation": (r_ac, w_ac), "bank": (r_bk, w_bk), "replies": (r_ar, w_ar), "tickets": (r_tk, w_tk), "apply": (r_ap, w_ap), "rolestatus": (r_rp, w_rp), "welcome": (r_wl, w_wl), "identity": (r_id, w_id),
      "azkar": (r_azkar, w_azkar), "levels": (r_levels, w_levels), "aladl": (r_adl, w_adl),
       "weapons": module_settings("weapons.db", "weapon_settings",
          ["panel_channel", "log_channel", "license_role"],
          ["embed_title", "embed_description", "embed_image_url", "show_guild_icon", "issue_price", "renew_price"]),
      "traffic": module_settings("bank_traffic.db", "traffic_settings",
          ["police_role", "control_channel", "citizen_channel", "log_channel"],
          ["control_title", "control_description", "control_image", "control_icon",
           "citizen_title", "citizen_description", "citizen_image", "citizen_icon"])}

@app.get("/api/guilds/{gid}/welcome-image")
async def welcome_image(gid: str, request: Request, slot: str = "1"):
    g = guild_or_403(request, gid)
    if slot not in WT: raise HTTPException(400, "نوع غير صالح")
    row = sqlite3.connect("welcome_goodbye.db").execute("SELECT image_url FROM welcome_settings WHERE guild_id=? AND type=?", (g.id, WT[slot])).fetchone()
    if not row or not row[0] or not os.path.isfile(row[0]): raise HTTPException(404, "الصورة غير موجودة")
    return FileResponse(row[0])

@app.get("/api/guilds/{gid}/settings/{section}")
async def read(gid: str, section: str, request: Request, slot: str = ""):
    g = guild_or_403(request, gid)
    if section not in SECTIONS:
        raise HTTPException(404)
    return AD[section][0](g, slot) if section in AD else get_settings(gid, section, slot)

@app.post("/api/guilds/{gid}/settings/{section}")
async def write(gid: str, section: str, request: Request, slot: str = ""):
    g = guild_or_403(request, gid)
    if section not in SECTIONS:
        raise HTTPException(404)
    data = await request.json()
    if not isinstance(data, dict) or len(json.dumps(data)) > 12_000_000:
        raise HTTPException(400, "بيانات غير صالحة")
    if section in ADMIN_ONLY:
        await require_admin(request, g)
    if section == "embed":
        await send_custom_embed(request, g, data)
        return {"ok": True, "panel": "sent"}
    if section in AD:
        AD[section][1](g, slot, data)
    else:
        save_settings(gid, section, data, slot)
    out = {"ok": True}
    configured_panel = False
    if section in PANEL_SENDERS and section in AD:
        try:
            current_settings = AD[section][0](g, slot)
            configured_panel = any(current_settings.get(k) for k in PANEL_KEYS.get(section, []))
        except Exception:
            configured_panel = False
    if configured_panel or any(data.get(k) for k in PANEL_KEYS.get(section, [])):
        out["panel"] = await send_panel(request.app.state.bot, g, section, slot)
    return out

# ---------- بانل التذاكر (إرسال مباشر من الموقع) ----------
@app.get("/api/guilds/{gid}/tickets/list")
async def tickets_list(gid: str, request: Request):
    g = guild_or_403(request, gid)
    d = jload(TKF).get(str(g.id), {})
    return [{"num": k, "name": v.get("name", "")} for k, v in sorted(d.items(), key=lambda x: int(x[0])) if isinstance(v, dict) and str(k).isdigit()]

def _panel_args(g, d):
    cid = str(d.get("channel_id", ""))
    ch = g.get_channel(int(cid)) if cid.isdigit() else None
    if not ch or str(getattr(ch, "type", "")) != "text":
        raise HTTPException(400, "حدد روم نصي صالح")
    title, desc = (d.get("title") or "").strip(), (d.get("desc") or "").strip()
    if not title or not desc:
        raise HTTPException(400, "اكتب عنوان ووصف البانل")
    if len(title) > 256 or len(desc) > 4000:
        raise HTTPException(400, "العنوان 256 حرف والوصف 4000 حرف كحد أقصى")
    image = (d.get("image") or "").strip() or None
    if image and not image.startswith(("http://", "https://")):
        raise HTTPException(400, "رابط الصورة لازم يبدأ بـ http أو https")
    nums = [str(n) for n in (d.get("nums") or []) if str(n).isdigit()]
    if not nums:
        raise HTTPException(400, "اختر عنصر واحد على الأقل")
    return ch, title, desc, image, nums, ("buttons" if d.get("display") == "buttons" else "dropdown")

async def _send_via_cog(bot, g, d, cog_name, fix_file):
    ch, title, desc, image, nums, disp = _panel_args(g, d)
    cog = bot.get_cog(cog_name)
    if not cog or not hasattr(cog, "send_panel"):
        raise HTTPException(500, "حدّث ملف %s أول" % fix_file)
    try:
        await cog.send_panel(g, ch.id, title, desc, image, disp, nums)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception:
        raise HTTPException(500, "تعذر إرسال البانل، تأكد من صلاحيات البوت في الروم")

async def do_send_ticket_panel(bot, g, d):
    await _send_via_cog(bot, g, d, "Ticket", "ticket.py")

async def do_send_apply_panel(bot, g, d):
    await _send_via_cog(bot, g, d, "SetupApply", "setup_apply.py")

@app.get("/api/guilds/{gid}/apply/list")
async def apply_list(gid: str, request: Request):
    g = guild_or_403(request, gid)
    d = jload(APF).get(str(g.id), {})
    if not isinstance(d, dict):
        return []
    out = []
    for k, v in d.items():
        if isinstance(v, dict) and str(k).isdigit():
            out.append({"num": str(k), "name": str(v.get("name") or "")})
    return sorted(out, key=lambda x: int(x["num"]))

@app.post("/api/guilds/{gid}/send/apply-panel")
async def send_apply_panel(gid: str, request: Request):
    g = guild_or_403(request, gid)
    d = await request.json()
    if not isinstance(d, dict):
        raise HTTPException(400, "بيانات غير صالحة")
    await do_send_apply_panel(request.app.state.bot, g, d)
    return {"ok": True}

@app.post("/api/guilds/{gid}/send/ticket-panel")
async def send_ticket_panel(gid: str, request: Request):
    g = guild_or_403(request, gid)
    d = await request.json()
    if not isinstance(d, dict):
        raise HTTPException(400, "بيانات غير صالحة")
    await do_send_ticket_panel(request.app.state.bot, g, d)
    return {"ok": True}

# ---------- تقييم الموقع ----------
db.execute("CREATE TABLE IF NOT EXISTS ratings(user_id TEXT PRIMARY KEY, stars INTEGER, comment TEXT, ts INTEGER, username TEXT DEFAULT '', avatar TEXT DEFAULT '')")
# ترقية قاعدة البيانات الموجودة دون حذف التقييمات السابقة
_rating_cols = {r[1] for r in db.execute("PRAGMA table_info(ratings)").fetchall()}
if "username" not in _rating_cols:
    db.execute("ALTER TABLE ratings ADD COLUMN username TEXT DEFAULT ''")
if "avatar" not in _rating_cols:
    db.execute("ALTER TABLE ratings ADD COLUMN avatar TEXT DEFAULT ''")
db.commit()

def rating_data(uid):
    m = db.execute("SELECT stars, comment FROM ratings WHERE user_id=?", (str(uid),)).fetchone()
    a = db.execute("SELECT AVG(stars), COUNT(*) FROM ratings").fetchone()
    return {"mine": {"stars": m[0], "comment": m[1] or ""} if m else None, "avg": round(a[0] or 0, 1), "count": a[1]}

def rating_save(uid, stars, comment, username="", avatar=""):
    import time
    try:
        stars = int(stars)
    except (TypeError, ValueError):
        stars = 0
    if not 1 <= stars <= 5:
        raise HTTPException(400, "اختر من 1 إلى 5 نجوم")
    comment = str(comment or "").strip()[:500]
    db.execute("REPLACE INTO ratings(user_id,stars,comment,ts,username,avatar) VALUES(?,?,?,?,?,?)", (str(uid), stars, comment, int(time.time()), str(username or "")[:100], str(avatar or "")[:500]))
    db.commit()

@app.get("/api/rating")
async def rating_get(request: Request):
    return rating_data(user_or_401(request)["id"])

@app.post("/api/rating")
async def rating_set(request: Request):
    u = user_or_401(request)
    d = await request.json()
    if not isinstance(d, dict):
        raise HTTPException(400, "بيانات غير صالحة")
    rating_save(u["id"], d.get("stars"), d.get("comment"), u.get("global_name") or u.get("name"), u.get("avatar"))
    return rating_data(u["id"])

@app.get("/api/ratings")
async def ratings_list(request: Request):
    user_or_401(request)
    rows = db.execute("SELECT stars,comment,ts,username,avatar FROM ratings ORDER BY ts DESC").fetchall()
    avg, count = db.execute("SELECT AVG(stars),COUNT(*) FROM ratings").fetchone()
    return {"avg": round(avg or 0, 1), "count": count, "items": [
        {"stars": int(r[0] or 0), "comment": r[1] or "", "ts": int(r[2] or 0),
         "name": r[3] or "عضو سابق", "avatar": r[4] or ""} for r in rows
    ]}

@app.get("/api/site-info")
async def site_info(request: Request):
    info = getattr(request.app.state, "application_info", None)
    created = None
    bot_id = getattr(info, "id", None) if info else None
    if bot_id:
        try:
            ts = ((int(bot_id) >> 22) + 1420070400000) / 1000
            created = datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).strftime("%Y/%m/%d")
        except Exception:
            pass
    return {"creator": {"username": ".a.506.", "avatar": "https://cdn.simpleicons.org/discord/5865F2"}, "created_at": created or "غير معروف"}

# ---------- تحسينات لوحة التحكم: إشعارات + دليل رموز الأخطاء ----------
# تُحقن هذه الإضافات في صفحة الواجهة عند تقديمها، لتظل التعديلات ضمن ملف Python.
DASHBOARD_EXTRAS = r"""
<style>
#t{position:fixed!important;top:18px!important;left:18px!important;right:auto!important;bottom:auto!important;z-index:99999!important;max-width:min(430px,calc(100vw - 36px));width:max-content;min-width:240px;padding:14px 18px 14px 16px!important;border-radius:15px!important;display:flex!important;align-items:center;gap:11px;opacity:0;visibility:hidden;transform:translateY(-12px);pointer-events:none;box-shadow:0 16px 42px #0007!important;transition:opacity .22s,transform .22s,visibility .22s!important;direction:rtl!important}
#t.show{opacity:1!important;visibility:visible!important;transform:translateY(0)!important}
#t:before{content:'✓';display:grid;place-items:center;flex:0 0 28px;width:28px;height:28px;border-radius:50%;font-weight:900;background:#1b9d68;color:#fff}
#t.er:before{content:'!';background:#e34d62;color:#fff}
#t:after{content:'يختفي تلقائيًا';display:block;font-size:10px;opacity:.68;position:absolute;bottom:3px;right:58px}
#nav a[data-s="errorcodes"]{cursor:pointer}
.error-codes-wrap{display:grid;gap:18px}.ec-tools{display:flex;gap:10px;flex-wrap:wrap}.ec-tools input,.ec-tools select{min-width:180px;flex:1;background:#111e39!important;color:#edf3ff!important;border:1px solid #344a78!important;border-radius:12px!important;padding:13px!important;font:inherit}.ec-count{color:#a9b8d6;font-size:13px}.ec-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,300px),1fr));gap:13px}.ec-card{background:linear-gradient(145deg,#172745,#101b32);border:1px solid #30466f;border-radius:17px;padding:17px;color:#eaf0ff;min-width:0}.ec-card-top{display:flex;align-items:center;gap:10px;margin-bottom:12px}.ec-symbol{width:40px;height:40px;border-radius:12px;display:grid;place-items:center;background:#e34d6220;color:#ff8797;font-size:20px;flex:none}.ec-code{font:700 13px ui-monospace,monospace;direction:ltr;text-align:left;color:#ff9aa8;background:#3a1d32;padding:5px 8px;border-radius:8px;margin-inline-start:auto}.ec-card h3{margin:0;font-size:16px}.ec-card p{margin:7px 0 0;line-height:1.8;color:#b9c7e0;font-size:13px}.ec-card b{color:#f0d6a0}.ec-empty{padding:24px;text-align:center;border:1px dashed #52658a;border-radius:14px;color:#b9c7e0}.ec-badge{display:inline-block;font-size:11px;padding:4px 8px;border-radius:99px;background:#253a61;color:#bcd0ff;margin-top:10px}
@media(max-width:600px){#t{top:10px!important;left:10px!important;max-width:calc(100vw - 20px);min-width:0}.ec-tools input,.ec-tools select{min-width:100%}}
</style>
<script>
(()=>{
'use strict';
const ERROR_CODES=[
['ERR-000','خطأ عام غير محدد','حدث خلل لم يتم تصنيفه بشكل واضح.','راجع سجل التشغيل، وحدد آخر إجراء قبل ظهور المشكلة، ثم افحص رسالة الخطأ الأصلية.','عام'],
['HTTP-400','طلب غير صالح','البيانات المرسلة ناقصة أو بتنسيق غير صحيح.','راجع الحقول المطلوبة وأنواع القيم، وتأكد من أن JSON صحيح.','API'],
['HTTP-401','يلزم تسجيل الدخول','انتهت الجلسة أو لا يوجد مستخدم مسجل.','سجّل الدخول من جديد، وتأكد من إرسال بيانات الجلسة مع الطلب.','API'],
['HTTP-403','لا توجد صلاحية','المستخدم لا يملك صلاحية تنفيذ العملية.','تحقق من صلاحيات إدارة السيرفر، ومن أن السيرفر ضمن السيرفرات المسموح بها.','صلاحيات'],
['HTTP-404','المورد غير موجود','الرابط أو السيرفر أو العنصر المطلوب غير موجود.','راجع الرابط والمعرّف، وتأكد من وجود البوت داخل السيرفر.','API'],
['HTTP-408','انتهت مهلة الطلب','استغرق الطلب وقتًا أطول من المهلة المحددة.','افحص اتصال الشبكة واستجابة الخدمة، ثم أعد المحاولة بعد التأكد من عدم تكرار العملية.','شبكة'],
['HTTP-429','طلبات كثيرة جدًا','تم تجاوز حد الطلبات المسموح به.','انتظر مدة التهدئة التي تحددها الخدمة، وقلل تكرار الطلبات ولا تكرر المحاولة بسرعة.','API'],
['HTTP-500','خطأ داخلي في الخادم','حدث استثناء داخل التطبيق.','راجع سجل الخادم وtraceback، وحدد الدالة التي سببت الاستثناء قبل إعادة المحاولة.','خادم'],
['HTTP-502','استجابة وسيطة غير صالحة','الخدمة الوسيطة لم تحصل على استجابة صحيحة.','تحقق من حالة الخدمة الخلفية وإعدادات البروكسي، ثم أعد المحاولة لاحقًا.','شبكة'],
['HTTP-503','الخدمة غير متاحة','الخدمة متوقفة مؤقتًا أو تحت ضغط.','تحقق من حالة الخدمة والموارد، ثم أعد المحاولة بعد عودة الخدمة.','خادم'],
['HTTP-504','انتهت مهلة البوابة','الخدمة الخلفية لم ترد في الوقت المحدد.','افحص زمن استجابة الخدمة الخلفية والاتصال بين الخوادم.','شبكة'],
['AUTH-001','فشل OAuth','تعذر إكمال تسجيل الدخول عبر Discord.','طابق Client ID وClient Secret وعنوان Redirect URI مع إعدادات تطبيق Discord.','تسجيل الدخول'],
['AUTH-002','عنوان إعادة التوجيه غير مطابق','عنوان callback لا يطابق العنوان المسجل.','ضع العنوان نفسه حرفيًا في إعدادات Discord وفي REDIRECT_URI، بما في ذلك البروتوكول والمنفذ.','تسجيل الدخول'],
['AUTH-003','رمز الوصول مرفوض أو منتهي','رمز OAuth لم يعد صالحًا.','أعد تسجيل الدخول واطلب رمزًا جديدًا، ولا تخزن الرموز في الواجهة العامة.','تسجيل الدخول'],
['AUTH-004','جلسة المستخدم غير موجودة','لم يتم العثور على بيانات المستخدم في الجلسة.','تأكد من إعداد SessionMiddleware وثبات مفتاح الجلسة ووجود ملفات تعريف الارتباط.','جلسة'],
['DIS-001','البوت غير موجود في السيرفر','تعذر العثور على السيرفر داخل قائمة سيرفرات البوت.','أضف البوت إلى السيرفر الصحيح، وتأكد من استخدام Server ID الصحيح.','Discord'],
['DIS-002','صلاحية Discord ناقصة','البوت لا يملك صلاحية تنفيذ الإجراء.','امنح البوت أقل مجموعة صلاحيات لازمة، وتحقق من صلاحيات القناة والرتبة.','Discord'],
['DIS-003','رتبة البوت منخفضة','رتبة البوت أدنى من الرتبة التي يحاول إدارتها.','ارفع رتبة البوت فوق الرتب المستهدفة ثم أعد تنفيذ العملية.','Discord'],
['DIS-004','قناة غير صالحة','معرّف القناة غير موجود أو لا يتبع السيرفر.','اختر قناة نصية صحيحة من قائمة قنوات السيرفر نفسه.','Discord'],
['DIS-005','رتبة غير صالحة','معرّف الرتبة غير موجود أو لا يتبع السيرفر.','اختر رتبة موجودة، وتأكد أنها ليست رتبة مُدارة لا يمكن للبوت التحكم بها.','Discord'],
['DIS-006','تعذر إرسال الرسالة','فشل إرسال رسالة أو لوحة إلى Discord.','تحقق من صلاحية عرض القناة وإرسال الرسائل وتضمين الروابط، ومن أن القناة غير محذوفة.','Discord'],
['DIS-007','تعذر جلب سجل التدقيق','البوت لا يستطيع قراءة Audit Log أو لا تتوفر البيانات.','امنح صلاحية View Audit Log وتأكد من أن الإجراء مسجل في Discord.','Discord'],
['NET-001','تعذر الاتصال بالخدمة','فشل الاتصال بسبب الشبكة أو انقطاع الخدمة.','افحص الاتصال وDNS والجدار الناري، ثم تحقق من أن عنوان الخدمة متاح.','شبكة'],
['NET-002','انقطع الاتصال أثناء الطلب','أُغلق الاتصال قبل اكتمال الرد.','راجع مهلات الاتصال، وأعد المحاولة بحذر بعد التأكد من أن العملية لم تنفذ جزئيًا.','شبكة'],
['NET-003','فشل DNS','تعذر تحويل اسم النطاق إلى عنوان IP.','تحقق من صحة اسم النطاق وإعدادات DNS واتصال الخادم.','شبكة'],
['JS-001','خطأ JavaScript','حدث خطأ أثناء تنفيذ سكربت الواجهة.','افتح أدوات المطور Console، واقرأ اسم الملف ورقم السطر، ثم أصلح الخطأ المحدد.','واجهة'],
['JS-002','عنصر HTML غير موجود','حاول السكربت الوصول إلى عنصر غير موجود في الصفحة.','تحقق من المعرّف المحدد في HTML، وانتظر تحميل DOM قبل استخدام العنصر.','واجهة'],
['JS-003','بيانات JSON غير صحيحة','تعذر تحليل JSON بسبب تنسيق غير صالح.','تحقق من الفواصل وعلامات الاقتباس والأقواس، واستخدم JSON.parse على نص صالح فقط.','بيانات'],
['JS-004','دالة غير معرّفة','استُدعيت دالة أو متغير غير موجود.','راجع الاسم ونطاق التعريف وترتيب تحميل ملفات JavaScript.','واجهة'],
['VAL-001','حقل مطلوب فارغ','لم تتم تعبئة حقل إلزامي.','أكمل الحقول المطلوبة قبل الحفظ، وأظهر اسم الحقل الناقص للمستخدم.','تحقق'],
['VAL-002','قيمة خارج النطاق','القيمة المدخلة خارج الحدود المقبولة.','راجع الحد الأدنى والأقصى ونوع القيمة، وأضف تحققًا قبل إرسال الطلب.','تحقق'],
['VAL-003','تنسيق رابط غير صالح','الرابط لا يبدأ ببروتوكول مدعوم أو غير قابل للتحليل.','استخدم رابطًا صحيحًا يبدأ بـ https:// أو http:// عند الحاجة.','تحقق'],
['DB-001','قاعدة البيانات غير جاهزة','تعذر الوصول إلى قاعدة البيانات أو جدول مطلوب.','تحقق من مسار قاعدة البيانات ووجود الجداول وصلاحيات القراءة والكتابة.','قاعدة بيانات'],
['DB-002','فشل حفظ البيانات','فشلت عملية INSERT أو UPDATE أو COMMIT.','راجع سجل SQLite ورسالة الاستثناء، وتأكد من أن الملف قابل للكتابة ولا توجد قيود مكسورة.','قاعدة بيانات'],
['DB-003','تعارض في البيانات','قد يكون هناك مفتاح مكرر أو قيد فريد منتهك.','تحقق من المفاتيح الأساسية والقيود، واستخدم معاملة واضحة عند تحديث السجلات.','قاعدة بيانات'],
['FILE-001','الملف غير موجود','تعذر العثور على ملف إعداد أو صورة.','تأكد من اسم الملف ومساره ومجلد التشغيل الحالي، وتحقق من حالة الأحرف.','ملفات'],
['FILE-002','تعذر قراءة الملف','لا يملك التطبيق صلاحية القراءة أو الملف غير قابل للقراءة.','تحقق من صلاحيات الملف وترميزه UTF-8 وسلامة محتواه.','ملفات'],
['FILE-003','تعذر حفظ الملف','فشل إنشاء الملف أو الكتابة إليه.','تحقق من صلاحيات المجلد والمساحة المتاحة وصحة مسار الحفظ.','ملفات'],
['CFG-001','إعداد ناقص','إعداد أساسي مفقود أو فارغ.','راجع متغيرات البيئة وملف الإعدادات، ولا تضع الأسرار داخل ملفات الواجهة العامة.','إعدادات'],
['CFG-002','منفذ مستخدم','المنفذ المحدد مشغول من عملية أخرى.','غيّر DASHBOARD_PORT أو أوقف الخدمة التي تستخدم المنفذ بعد التحقق منها.','إعدادات'],
['BOT-001','ملف إضافة البوت غير محمّل','الإضافة المطلوبة غير موجودة في قائمة إضافات البوت.','تأكد من اسم ملف الإضافة وطريقة تحميلها، ثم راجع سجل بدء تشغيل البوت.','بوت'],
['BOT-002','تعذر العثور على صنف مطلوب','الصنف أو الواجهة البرمجية المتوقعة غير موجودة في الإضافة.','راجع اسم الصنف وتوافق إصدار الإضافة مع الكود الذي يستدعيه.','بوت'],
['BOT-003','البوت غير متصل','البوت غير جاهز أو لم يكتمل تسجيل دخوله.','انتظر حدث الجاهزية، وتحقق من سجل التشغيل واتصال Discord.','بوت'],
['SEC-001','رفض الوصول إلى إعدادات محمية','الحساب لا يملك صلاحية تعديل القسم.','استخدم حسابًا مخولًا وتحقق من صلاحيات المستخدم في السيرفر قبل السماح بالتعديل.','أمان'],
['SEC-002','مفتاح جلسة ضعيف أو ثابت افتراضيًا','مفتاح جلسة غير مناسب قد يعرّض الجلسات للخطر.','أنشئ مفتاحًا عشوائيًا قويًا من متغير بيئة، وغيّره إذا انكشف ولا ترفعه إلى المستودع.','أمان']
];
const $x=s=>document.querySelector(s);
function showErrorCodes(){
 const main=$x('#main');if(!main)return;
 main.classList.add('sec');
 main.innerHTML=`<div class="ph"><span class="pi">🧩</span><div><h1>رموز الأخطاء</h1><small>دليل تشخيصي للأخطاء الشائعة والحلول المقترحة</small></div></div><div class="error-codes-wrap"><div class="card" style="display:block"><div class="ec-tools"><input id="ec-search" type="search" placeholder="ابحث بالرمز أو اسم الخطأ أو الحل..." aria-label="ابحث في رموز الأخطاء"><select id="ec-category" aria-label="تصفية حسب التصنيف"><option value="">كل التصنيفات</option>${[...new Set(ERROR_CODES.map(x=>x[4]))].sort().map(c=>`<option>${c}</option>`).join('')}</select></div><p id="ec-count" class="ec-count"></p></div><div id="ec-grid" class="ec-grid"></div></div>`;
 const render=()=>{const q=($x('#ec-search').value||'').trim().toLowerCase(),cat=$x('#ec-category').value;const rows=ERROR_CODES.filter(x=>(!cat||x[4]===cat)&&x.join(' ').toLowerCase().includes(q));$x('#ec-count').textContent=`عرض ${rows.length} من ${ERROR_CODES.length} رمزًا`; $x('#ec-grid').innerHTML=rows.length?rows.map(x=>`<article class="ec-card"><div class="ec-card-top"><span class="ec-symbol">⚠</span><h3>${x[1]}</h3><span class="ec-code">${x[0]}</span></div><p><b>السبب:</b> ${x[2]}</p><p><b>الحل المقترح:</b> ${x[3]}</p><span class="ec-badge">${x[4]}</span></article>`).join(''):'<div class="ec-empty">ما لقينا رموز تطابق بحثك.</div>'};
 $x('#ec-search').addEventListener('input',render);$x('#ec-category').addEventListener('change',render);render();
}
function ensureErrorNav(){
 const nav=$x('#nav');if(!nav||nav.querySelector('[data-s="errorcodes"]'))return;
 const group=document.createElement('div');group.className='ng';group.textContent='التشخيص والدعم';
 const link=document.createElement('a');link.dataset.s='errorcodes';link.innerHTML='<span>🧩</span>رموز الأخطاء';link.href='#errorcodes';link.addEventListener('click',e=>{e.preventDefault();showErrorCodes();nav.querySelectorAll('a').forEach(a=>a.classList.toggle('active',a===link));});
 nav.append(group,link);
}
function toastEnhanced(message,ok=1){const t=$x('#t');if(!t)return;clearTimeout(t._);const m=String(message??'حدث خطأ');t.textContent='';const icon=document.createElement('span');icon.className='toast-icon';icon.textContent=ok?'✓':'!';icon.style.cssText='display:none';t.appendChild(document.createTextNode(m));t.className='show '+(ok?'ok':'er');t.setAttribute('role','status');t.setAttribute('aria-live',ok?'polite':'assertive');t.dataset.errorCode=ok?'':guessCode(m);if(!ok){const code=document.createElement('small');code.textContent='رمز تشخيصي: '+t.dataset.errorCode;code.style.cssText='display:block;font-size:10px;opacity:.8;margin-right:auto';t.appendChild(code)}t._=setTimeout(()=>{t.className='';t.textContent='';},4500)}
function guessCode(m){const s=m.toLowerCase();if(/401|سجّل دخول|تسجيل الدخول/.test(s))return'AUTH-001';if(/403|صلاحية|الأدمن فقط|ما عندك صلاحية/.test(s))return'HTTP-403';if(/404|غير موجود|مو موجود/.test(s))return'HTTP-404';if(/429|طلبات كثيرة/.test(s))return'HTTP-429';if(/json|صيغة غير صحيحة/.test(s))return'JS-003';if(/قاعدة بيانات|قاعدة البيانات|sqlite|تعذر حفظ/.test(s))return'DB-002';if(/روم|قناة/.test(s))return'DIS-004';if(/رتبة/.test(s))return'DIS-005';if(/شبكة|اتصال|connection|timeout/.test(s))return'NET-001';if(/مطلوب|اختر|اكتب/.test(s))return'VAL-001';if(/bot|بوت/.test(s))return'DIS-001';if(/500|الخادم|server error/.test(s))return'HTTP-500';return'ERR-000'}
function initExtras(){ensureErrorNav();const nav=$x('#nav');if(nav&&!nav.dataset.errObserver){new MutationObserver(ensureErrorNav).observe(nav,{childList:true});nav.dataset.errObserver='1'}window.toast=toastEnhanced;const old=$x('#t');if(old){old.classList.remove('show');old.textContent=''} }
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',initExtras);else initExtras();
})();
</script>
"""

@app.middleware("http")
async def inject_dashboard_extras(request: Request, call_next):
    response = await call_next(request)
    if request.method == "GET" and request.url.path in ("/", "/index.html") and "text/html" in response.headers.get("content-type", ""):
        chunks = []
        async for chunk in response.body_iterator:
            chunks.append(chunk)
        body = b"".join(chunks)
        try:
            html = body.decode("utf-8")
            if "DASHBOARD_EXTRAS" not in html and "رموز الأخطاء" not in html:
                html = html.replace("</body>", DASHBOARD_EXTRAS + "\n</body>")
            headers = {k: v for k, v in response.headers.items() if k.lower() not in ("content-length", "content-encoding", "transfer-encoding")}
            return Response(content=html, status_code=response.status_code, headers=headers, media_type="text/html")
        except UnicodeDecodeError:
            return Response(content=body, status_code=response.status_code, headers=dict(response.headers), media_type="text/html")
    return response

# الواجهة (مجلد web/index.html) — يجب أن يكون آخر شي
app.mount("/", StaticFiles(directory="web", html=True), name="web")

async def start_web(bot):
    app.state.bot = bot
    try:
        app.state.application_info = await bot.application_info()
    except Exception:
        app.state.application_info = None
    cfg = uvicorn.Config(app, host="0.0.0.0", port=PORT, log_level="warning")
    await uvicorn.Server(cfg).serve()
