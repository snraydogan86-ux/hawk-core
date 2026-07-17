import os
import json
import re
import sqlite3
from pathlib import Path
from datetime import datetime

BASE_DIR = Path(os.getenv("HAWK_MEMORY_DIR", "/data/hawk_memory"))
BASE_DIR.mkdir(parents=True, exist_ok=True)

MEMORY_FILE = BASE_DIR / "memory.json"
DB_FILE = BASE_DIR / "memory.sqlite3"


def _db():
    conn = sqlite3.connect(str(DB_FILE))
    conn.row_factory = sqlite3.Row
    return conn


def _init_db():
    conn = _db()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS user_facts (
            user_id TEXT NOT NULL,
            key TEXT NOT NULL,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (user_id, key)
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS user_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            ts TEXT NOT NULL
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_user_history_user_id_id ON user_history(user_id, id)")
    # F3: confidence + provenance (kaynak). Düşük güvenli çıkarım, yüksek güvenli
    # mevcut fact'i ezmesin diye kullanılır. Idempotent ALTER.
    for _col, _decl in (("confidence", "REAL DEFAULT 0.9"), ("source", "TEXT DEFAULT 'explicit'"),
                        ("conversation_id", "TEXT DEFAULT ''"), ("extraction_method", "TEXT DEFAULT 'pattern'")):
        try:
            cur.execute(f"ALTER TABLE user_facts ADD COLUMN {_col} {_decl}")
        except Exception:
            pass
    conn.commit()
    conn.close()


_init_db()


def _load():
    if not MEMORY_FILE.exists():
        return {"facts": {}, "history": []}
    try:
        return json.loads(MEMORY_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"facts": {}, "history": []}


def _save(data):
    MEMORY_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _hawk_clean_preference_value(value: str) -> str:
    s = str(value or "").strip()
    s = re.sub(r"\s+", " ", s).strip()

    tails = [
        "bunu hatırla",
        "bunu unutma",
        "hatırla",
        "unutma",
        "aklında tut",
        "remember this",
        "remember that",
    ]

    for tail in tails:
        s = re.sub(r"[\s,.;:!?-]" + re.escape(tail) + r"[\s,.;:!?-]$", "", s, flags=re.I).strip()

    s = re.sub(r"[.]{2,}$", ".", s).strip()
    s = s.strip(" \t\r\n,.;:!?-\"'“”‘’")
    return s

def _hawk_clean_person_name(value: str) -> str:
    s = str(value or "").strip()
    tails = [
        "bunu hatırla", "bunu unutma", "hatırla", "unutma",
        "aklında tut", "remember this", "remember me as",
    ]
    s = s.strip(" \t\r\n\"'“”‘’")
    s = re.sub(r"\s+", " ", s)

    for tail in tails:
        s = re.sub(r"[\s,.:;!?-]*" + re.escape(tail) + r"[\s,.:;!?-]*$", "", s, flags=re.I).strip()

    prefixes = ["benim adım ", "adım ", "my name is ", "i am ", "i'm "]
    low = s.lower()
    for pref in prefixes:
        if low.startswith(pref):
            s = s[len(pref):].strip()
            break

    s = re.split(r"(?<=[A-Za-zÇĞİÖŞÜçğıöşü])\.\s+", s)[0].strip()
    s = s.strip(" \t\r\n,.;:!?-\"'“”‘’")

    words = s.split()
    if len(words) > 4:
        s = " ".join(words[:4]).strip()

    bad_names = {
        "", "ne", "nedir", "kim", "kimi", "what", "who", "name",
        "adım ne", "benim adım ne", "what is my name", "my name"
    }
    low = s.lower().strip()
    if low in bad_names:
        return ""

    if "?" in s:
        return ""

    return s


def _normalize_user_id(user_id: str | None) -> str:
    return str(user_id or "default_user").strip() or "default_user"


def remember_fact(key: str, value: str, user_id: str = "default_user",
                  confidence: float = 0.9, source: str = "explicit",
                  conversation_id: str = "", extraction_method: str = "pattern"):
    """Fact kaydet. confidence + provenance + timestamp + conversation_id +
    extraction_method tutulur; DÜŞÜK güvenli çıkarım, farklı değere sahip YÜKSEK
    güvenli mevcut fact'i EZMEZ (merge/çelişki kuralı)."""
    user_id = _normalize_user_id(user_id)

    if str(key) in ("name", "user_name", "full_name", "display_name"):
        value = _hawk_clean_person_name(value)
        if not str(value or "").strip():
            return False
    else:
        value = _hawk_clean_preference_value(value)

    if not str(value or "").strip():
        return False

    try:
        confidence = float(confidence)
    except Exception:
        confidence = 0.9

    ts = datetime.utcnow().isoformat()

    conn = _db()
    cur = conn.cursor()
    # Mevcut fact'in güvenini kontrol et — düşük güvenli, yüksek güvenliyi ezmesin.
    try:
        cur.execute("SELECT value, confidence FROM user_facts WHERE user_id=? AND key=?",
                    (user_id, str(key)))
        ex = cur.fetchone()
        if ex is not None:
            try:
                ex_conf = float(ex["confidence"]) if ex["confidence"] is not None else 0.9
            except Exception:
                ex_conf = 0.9
            if confidence < ex_conf and str(ex["value"]).strip().lower() != str(value).strip().lower():
                conn.close()
                return False
    except Exception:
        pass

    cur.execute("""
        INSERT INTO user_facts (user_id, key, value, updated_at, confidence, source, conversation_id, extraction_method)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(user_id, key) DO UPDATE SET
            value=excluded.value,
            updated_at=excluded.updated_at,
            confidence=excluded.confidence,
            source=excluded.source,
            conversation_id=excluded.conversation_id,
            extraction_method=excluded.extraction_method
    """, (user_id, str(key), str(value), ts, confidence, str(source),
          str(conversation_id or ""), str(extraction_method or "pattern")))
    conn.commit()
    conn.close()

    if user_id == "default_user":
        data = _load()
        data["facts"][key] = {"value": value, "updated_at": ts}
        _save(data)

    return True


# ═══════════════════ SİLME (unut/forget) — gerçek, kalıcı ════════════════════
import uuid as _uuid
import hashlib as _hashlib


def _audit_delete(user_id, keys, scope, nf, nh, request_id):
    """Audit: yalnız METADATA (key adları, kapsam, sayı) — silinen DEĞER tutulmaz.
    user_scope = user_id'nin hash'i (KVKK: düz email/değer loglanmaz)."""
    try:
        conn = _db(); cur = conn.cursor()
        cur.execute("""CREATE TABLE IF NOT EXISTS memory_deletion_audit (
            request_id TEXT, user_scope TEXT, keys TEXT, scope TEXT,
            deleted_facts INTEGER, deleted_history INTEGER, ts TEXT)""")
        us = _hashlib.sha256(str(user_id).encode()).hexdigest()[:16]
        cur.execute("INSERT INTO memory_deletion_audit VALUES (?,?,?,?,?,?,?)",
                    (request_id, us, ",".join(keys or []), scope, int(nf), int(nh),
                     datetime.utcnow().isoformat()))
        conn.commit(); conn.close()
    except Exception:
        pass


def _result(nf=0, nh=0, keys=None, scope="fact", rid=None, success=True):
    return {"success": bool(success), "deleted_facts": int(nf), "deleted_history": int(nh),
            "keys": list(keys or []), "scope": scope, "request_id": rid or _uuid.uuid4().hex[:12]}


def forget_fact(user_id, key):
    user_id = _normalize_user_id(user_id); rid = _uuid.uuid4().hex[:12]
    conn = _db(); cur = conn.cursor()
    cur.execute("DELETE FROM user_facts WHERE user_id=? AND key=?", (user_id, str(key)))
    n = cur.rowcount or 0; conn.commit(); conn.close()
    if user_id == "default_user":
        try:
            d = _load(); d["facts"].pop(key, None); _save(d)
        except Exception:
            pass
    _audit_delete(user_id, [key] if n else [], "fact", n, 0, rid)
    return _result(n, 0, [key] if n else [], "fact", rid)


def forget_keys(user_id, keys):
    user_id = _normalize_user_id(user_id); rid = _uuid.uuid4().hex[:12]
    deleted = []
    conn = _db(); cur = conn.cursor()
    for k in (keys or []):
        cur.execute("DELETE FROM user_facts WHERE user_id=? AND key=?", (user_id, str(k)))
        if cur.rowcount:
            deleted.append(k)
    conn.commit(); conn.close()
    _audit_delete(user_id, deleted, "keys", len(deleted), 0, rid)
    return _result(len(deleted), 0, deleted, "keys", rid)


def forget_last_fact(user_id):
    user_id = _normalize_user_id(user_id); rid = _uuid.uuid4().hex[:12]
    conn = _db(); cur = conn.cursor()
    row = cur.execute("SELECT key FROM user_facts WHERE user_id=? ORDER BY updated_at DESC LIMIT 1",
                      (user_id,)).fetchone()
    n = 0; keys = []
    if row:
        cur.execute("DELETE FROM user_facts WHERE user_id=? AND key=?", (user_id, row["key"]))
        n = cur.rowcount or 0; keys = [row["key"]] if n else []
    conn.commit(); conn.close()
    _audit_delete(user_id, keys, "last", n, 0, rid)
    return _result(n, 0, keys, "last", rid)


def forget_conversation_facts(user_id, conversation_id):
    user_id = _normalize_user_id(user_id); rid = _uuid.uuid4().hex[:12]
    conn = _db(); cur = conn.cursor()
    rows = cur.execute("SELECT key FROM user_facts WHERE user_id=? AND conversation_id=?",
                       (user_id, str(conversation_id or ""))).fetchall()
    keys = [r["key"] for r in rows]
    cur.execute("DELETE FROM user_facts WHERE user_id=? AND conversation_id=? AND conversation_id!=''",
                (user_id, str(conversation_id or "")))
    n = cur.rowcount or 0; conn.commit(); conn.close()
    _audit_delete(user_id, keys, "conversation", n, 0, rid)
    return _result(n, 0, keys, "conversation", rid)


def forget_all_facts(user_id):
    user_id = _normalize_user_id(user_id); rid = _uuid.uuid4().hex[:12]
    conn = _db(); cur = conn.cursor()
    keys = [r["key"] for r in cur.execute("SELECT key FROM user_facts WHERE user_id=?", (user_id,)).fetchall()]
    cur.execute("DELETE FROM user_facts WHERE user_id=?", (user_id,))
    n = cur.rowcount or 0; conn.commit(); conn.close()
    _audit_delete(user_id, keys, "all_facts", n, 0, rid)
    return _result(n, 0, keys, "all_facts", rid)


def forget_history(user_id):
    user_id = _normalize_user_id(user_id); rid = _uuid.uuid4().hex[:12]
    conn = _db(); cur = conn.cursor()
    cur.execute("DELETE FROM user_history WHERE user_id=?", (user_id,))
    n = cur.rowcount or 0; conn.commit(); conn.close()
    _audit_delete(user_id, [], "history", 0, n, rid)
    return _result(0, n, [], "history", rid)


def forget_all_user_data(user_id):
    rid = _uuid.uuid4().hex[:12]
    rf = forget_all_facts(user_id)
    rh = forget_history(user_id)
    _audit_delete(_normalize_user_id(user_id), rf["keys"], "all", rf["deleted_facts"],
                  rh["deleted_history"], rid)
    return _result(rf["deleted_facts"], rh["deleted_history"], rf["keys"], "all", rid)


def _ensure_retry_table(cur):
    cur.execute("""CREATE TABLE IF NOT EXISTS deletion_retry (
        request_id TEXT, user_id TEXT, store TEXT, scope TEXT, cats TEXT,
        attempts INTEGER DEFAULT 0, ts TEXT)""")


def queue_deletion_retry(user_id, scope, cats, request_id, store="cloud"):
    """Başarısız store silmesi → dead-letter kuyruğu (idempotent retry için)."""
    try:
        conn = _db(); cur = conn.cursor(); _ensure_retry_table(cur)
        cur.execute("INSERT INTO deletion_retry VALUES (?,?,?,?,?,0,?)",
                    (request_id, _normalize_user_id(user_id), store, scope,
                     ",".join(cats or []), datetime.utcnow().isoformat()))
        conn.commit(); conn.close()
    except Exception:
        pass


def list_pending_deletions():
    try:
        conn = _db(); cur = conn.cursor(); _ensure_retry_table(cur)
        rows = cur.execute("SELECT rowid, request_id, user_id, store, scope, cats, attempts "
                           "FROM deletion_retry").fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception:
        return []


def clear_deletion_retry(rowid):
    try:
        conn = _db(); cur = conn.cursor()
        cur.execute("DELETE FROM deletion_retry WHERE rowid=?", (rowid,))
        conn.commit(); conn.close()
    except Exception:
        pass


def bump_deletion_attempt(rowid):
    try:
        conn = _db(); cur = conn.cursor()
        cur.execute("UPDATE deletion_retry SET attempts=attempts+1 WHERE rowid=?", (rowid,))
        conn.commit(); conn.close()
    except Exception:
        pass


def recall_fact(key: str, user_id: str = "default_user"):
    user_id = _normalize_user_id(user_id)

    conn = _db()
    cur = conn.cursor()
    cur.execute("SELECT value FROM user_facts WHERE user_id = ? AND key = ?", (user_id, str(key)))
    row = cur.fetchone()
    conn.close()

    if row:
        return row["value"]

    if user_id == "default_user":
        data = _load()
        item = data["facts"].get(key)
        if item:
            return item.get("value")

    return None


def add_history(role: str, content: str, user_id: str = "default_user"):
    user_id = _normalize_user_id(user_id)
    # SECRET-REDAKSİYON: hafıza/geçmişe secret (api key/private key/token/jwt) YAZILMAZ.
    try:
        from core.secret_redactor import redact_secrets
        content = redact_secrets(str(content))[0]
    except Exception:
        pass
    # PII-REDAKSİYON: hafıza/geçmişe TC/telefon/kart YAZILMAZ (KVKK/gizlilik).
    try:
        from core.pii_redactor import redact_pii
        content = redact_pii(str(content))[0]
    except Exception:
        pass
    ts = datetime.utcnow().isoformat()

    conn = _db()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO user_history (user_id, role, content, ts)
        VALUES (?, ?, ?, ?)
    """, (user_id, str(role), str(content), ts))
    cur.execute("""
        DELETE FROM user_history
        WHERE user_id = ?
          AND id NOT IN (
            SELECT id FROM user_history
            WHERE user_id = ?
            ORDER BY id DESC
            LIMIT 30
          )
    """, (user_id, user_id))
    conn.commit()
    conn.close()

    if user_id == "default_user":
        data = _load()
        data["history"].append({"role": role, "content": content, "ts": ts})
        data["history"] = data["history"][-30:]
        _save(data)


def recent_history(limit: int = 8, user_id: str = "default_user"):
    user_id = _normalize_user_id(user_id)

    conn = _db()
    cur = conn.cursor()
    cur.execute("""
        SELECT role, content, ts
        FROM user_history
        WHERE user_id = ?
        ORDER BY id DESC
        LIMIT ?
    """, (user_id, int(limit)))
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    rows.reverse()

    if rows:
        return rows

    if user_id == "default_user":
        data = _load()
        return data["history"][-limit:]

    return []


def memory_snapshot(user_id: str = "default_user"):
    user_id = _normalize_user_id(user_id)

    conn = _db()
    cur = conn.cursor()
    cur.execute("SELECT key, value FROM user_facts WHERE user_id = ?", (user_id,))
    facts = {row["key"]: row["value"] for row in cur.fetchall()}
    try:
        _snap_turns = int(os.getenv("HAWK_MEM_SNAPSHOT_TURNS", "14"))
    except Exception:
        _snap_turns = 14
    cur.execute("""
        SELECT role, content, ts
        FROM user_history
        WHERE user_id = ?
        ORDER BY id DESC
        LIMIT ?
    """, (user_id, _snap_turns))
    history = [dict(r) for r in cur.fetchall()]
    conn.close()
    history.reverse()

    if facts or history:
        return {"facts": facts, "history": history}

    if user_id == "default_user":
        data = _load()
        facts = {k: v.get("value") for k, v in data.get("facts", {}).items()}
        return {"facts": facts, "history": data.get("history", [])[-8:]}

    return {"facts": {}, "history": []}


def _hawk_extract_preference_facts(msg: str):
    text = str(msg or "").strip()
    low = text.lower()
    stored = []

    def put(key: str, value: str):
        v = _hawk_clean_preference_value(value)
        if v:
            stored.append((key, v))

    if "türkçe cevap ver" in low or "turkce cevap ver" in low:
        put("language_preference", "Türkçe")

    if "ingilizce cevap ver" in low or "answer in english" in low or "english reply" in low:
        put("language_preference", "English")

    m = re.search(r"bana\s+(.+?)\s+diye\s+hitap\s+et", text, flags=re.I)
    if m:
        value = m.group(1).strip()
        value = re.sub(r"^(hawk|assistant)\s+diye\s+değil\s+", "", value, flags=re.I)
        value = re.sub(r"^(hawk|assistant)\s+diye\s+degil\s+", "", value, flags=re.I)
        if re.search(r"\bdeğil\b", value, flags=re.I):
            value = re.split(r"\bdeğil\b", value, flags=re.I)[-1].strip()
        if re.search(r"\bdegil\b", value, flags=re.I):
            value = re.split(r"\bdegil\b", value, flags=re.I)[-1].strip()
        value = value.strip(" ,.;:!-")
        return stored

    m = re.search(r"(?:cevap|yanıt) tonu\s+(.+?)\s+olsun", text, flags=re.I)
    if m:
        put("tone_preference", m.group(1))
    elif "kısa ve net" in low or "kisa ve net" in low:
        put("tone_preference", "kısa ve net")
    elif "net ve kısa" in low:
        put("tone_preference", "net ve kısa")

    m = re.search(r"call me\s+(.+?)(?:[\.!?,]|$)", text, flags=re.I)
    if m:
        return stored

    m = re.search(r"my preferred voice is\s+(.+?)(?:[\.!?,]|$)", text, flags=re.I)
    if m:
        put("voice_preference", m.group(1))

    m = re.search(r"ses tercihim\s+(.+)", text, flags=re.I)
    if m:
        put("voice_preference", m.group(1))

    return stored


_QUESTION_MARKERS = (
    "?", "neydi", "nedir", "ne oldu", "adım ne", "adim ne", "ismim ne", "adın ne",
    "ben kimim", "kimim ben", "kim miyim", "nerede", "nereye", "hangi", "kaç yaşında",
    "kac yasinda", "what is my", "what's my", "who am i", "where do i", "where am i",
    "which", "do you remember", "hatırlıyor musun", "hatirliyor musun", "biliyor musun",
)


def _looks_like_question(text: str) -> bool:
    """F3: soru cümlelerini fact olarak KAYDETME. 'Benim adım neydi?', 'Nerede
    çalışıyordum?', 'Ben kimim?' gibi ifadeler mevcut bilgiyi ezmemeli."""
    t = (text or "").strip().lower()
    if not t:
        return True
    return any(m in t for m in _QUESTION_MARKERS)


def _first_clause(value: str) -> str:
    """İsim/rol/proje çıkarımını bağlaç ve noktalama sınırında durdur.
    'Zümrüt ve projem Falcon-X' → 'Zümrüt'."""
    v = str(value or "").strip()
    v = re.split(r"\s+(?:ve|ile|and|ayrıca)\s+|[,;.!?]|\s+ki\s+|\s+çünkü\s+",
                 v, maxsplit=1, flags=re.I)[0]
    return v.strip(" .,!?:;\"'“”‘’")


def extract_and_store_user_facts(message: str, user_id: str = "default_user",
                                 conversation_id: str = "", extra_facts=None):
    """HİBRİT fact extraction (pattern katmanı + dışarıdan gelen LLM structured
    fact'lerin merge'i). extra_facts: [{key,value,confidence,source,method}] —
    app tarafındaki LLM extractor'dan gelir. Merge/çelişki kuralı remember_fact'te
    (düşük confidence yükseği ezmez)."""
    # SQLite katmanı (backward-compat). Merkezi çok-store yazımı için
    # memory_pipeline.write_message_facts kullanılır (bu, extract_facts + tüm store'lar).
    stored = []
    for f in extract_facts(message, conversation_id=conversation_id, extra_facts=extra_facts):
        try:
            if remember_fact(f["key"], f["value"], user_id=user_id,
                             confidence=f["confidence"], source=f["source"],
                             conversation_id=f.get("conversation_id", conversation_id),
                             extraction_method=f["extraction_method"]):
                stored.append((f["key"], f["value"]))
        except Exception:
            pass
    return stored


def extract_facts(message: str, conversation_id: str = "", extra_facts=None):
    """SAF çıkarım — hiçbir store'a YAZMAZ; fact listesi döndürür (pipeline kullanır).
    Döner: [{key, value, confidence, source, extraction_method, conversation_id}, ...]"""
    msg = (message or "").strip()
    low = msg.lower()
    _is_q = _looks_like_question(msg)
    out = []

    def _add(k, v, conf, src, method):
        k = str(k or "").strip(); v = str(v or "").strip()
        if k and v:
            out.append({"key": k, "value": v, "confidence": float(conf), "source": src,
                        "extraction_method": method, "conversation_id": conversation_id})

    try:
        for k, v in _hawk_extract_preference_facts(msg):
            _add(k, v, 0.85, "pattern", "pattern")
    except Exception:
        pass

    if not _is_q and "ben " in low and " yaşındayım" in low:
        try:
            part = low.split("ben ", 1)[1].split(" yaşındayım", 1)[0].strip()
            if part.isdigit():
                _add("user_age", part, 0.95, "explicit", "pattern")
        except Exception:
            pass

    try:
        from core.fact_extractor import pattern_extract
        for key, val, conf, method in pattern_extract(msg):
            _add(key, val, conf, "pattern", method)
    except Exception:
        pass

    for f in (extra_facts or []):
        try:
            _add(f.get("key"), f.get("value"), float(f.get("confidence", 0.8)),
                 str(f.get("source", "llm")), str(f.get("method", "llm")))
        except Exception:
            pass
    return out


# ── Write-retry (eventual consistency: cloud store başarısız yazım) ──
def _ensure_write_retry(cur):
    cur.execute("""CREATE TABLE IF NOT EXISTS write_retry (
        user_id TEXT, key TEXT, value TEXT, confidence REAL, source TEXT,
        conversation_id TEXT, extraction_method TEXT, store TEXT,
        attempts INTEGER DEFAULT 0, ts TEXT)""")


def queue_write_retry(user_id, key, value, confidence, source, conversation_id,
                      extraction_method, store):
    try:
        conn = _db(); cur = conn.cursor(); _ensure_write_retry(cur)
        cur.execute("INSERT INTO write_retry VALUES (?,?,?,?,?,?,?,?,0,?)",
                    (_normalize_user_id(user_id), str(key), str(value), float(confidence or 0.8),
                     str(source), str(conversation_id or ""), str(extraction_method or "pattern"),
                     str(store), datetime.utcnow().isoformat()))
        conn.commit(); conn.close()
    except Exception:
        pass


def list_pending_writes():
    try:
        conn = _db(); cur = conn.cursor(); _ensure_write_retry(cur)
        rows = cur.execute("SELECT rowid, user_id, key, value, confidence, source, "
                           "conversation_id, extraction_method, store, attempts FROM write_retry").fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception:
        return []


def clear_write_retry(rowid):
    try:
        conn = _db(); cur = conn.cursor()
        cur.execute("DELETE FROM write_retry WHERE rowid=?", (rowid,)); conn.commit(); conn.close()
    except Exception:
        pass


def bump_write_attempt(rowid):
    try:
        conn = _db(); cur = conn.cursor()
        cur.execute("UPDATE write_retry SET attempts=attempts+1 WHERE rowid=?", (rowid,))
        conn.commit(); conn.close()
    except Exception:
        pass
