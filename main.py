import datetime
import json
import sqlite3
from typing import Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel, Field, RootModel, field_validator

DB_PATH = "eatsmart.db"
LOCAL_TZ = ZoneInfo("America/Los_Angeles")
DAY_TYPES = {"rest", "training"}

app = FastAPI(title="EatSmart API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def now_ts() -> str:
    # Use Pacific Time as the canonical local time for all stored timestamps.
    return datetime.datetime.now(LOCAL_TZ).isoformat()


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS food_templates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            category TEXT,
            base_quantity REAL NOT NULL DEFAULT 100,
            nutrients_json TEXT NOT NULL,
            favorite INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS meals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            day_type TEXT DEFAULT 'rest',
            meal_time TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS food_intakes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            template_id INTEGER,
            meal_id INTEGER,
            name TEXT NOT NULL,
            category TEXT,
            grams REAL NOT NULL,
            normalized_nutrients_json TEXT NOT NULL,
            intake_at TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(template_id) REFERENCES food_templates(id) ON DELETE SET NULL,
            FOREIGN KEY(meal_id) REFERENCES meals(id) ON DELETE SET NULL
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS user_profile (
            id INTEGER PRIMARY KEY CHECK(id = 1),
            height_cm REAL,
            weight_kg REAL,
            age INTEGER,
            sex TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS nutrient_goals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nutrient TEXT NOT NULL,
            day_type TEXT NOT NULL DEFAULT 'rest',
            min_per_day REAL,
            max_per_day REAL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(nutrient, day_type)
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS day_types (
            date TEXT PRIMARY KEY,
            day_type TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS weight_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            value_kg REAL NOT NULL,
            note TEXT,
            logged_at TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    # Backfill schema changes for existing DBs.
    def column_exists(table: str, column: str) -> bool:
        res = conn.execute(f"PRAGMA table_info({table})").fetchall()
        return any(r[1] == column for r in res)

    if not column_exists("food_intakes", "meal_id"):
        conn.execute("ALTER TABLE food_intakes ADD COLUMN meal_id INTEGER")
    if not column_exists("meals", "day_type"):
        conn.execute("ALTER TABLE meals ADD COLUMN day_type TEXT DEFAULT 'rest'")
    if not column_exists("nutrient_goals", "day_type"):
        conn.execute("ALTER TABLE nutrient_goals ADD COLUMN day_type TEXT DEFAULT 'rest'")
        conn.execute("UPDATE nutrient_goals SET day_type = 'rest' WHERE day_type IS NULL")

    conn.commit()
    conn.close()


init_db()


# --------- Models ---------
class NutrientMap(RootModel[Dict[str, float]]):
    @field_validator("root")
    @classmethod
    def validate_nutrients(cls, v: Dict[str, float]) -> Dict[str, float]:
        if not isinstance(v, dict) or not v:
            raise ValueError("nutrients must be a non-empty object")
        cleaned = {}
        for k, val in v.items():
            if not isinstance(val, (int, float)):
                raise ValueError(f"Nutrient {k} must be a number")
            if val < 0:
                raise ValueError(f"Nutrient {k} cannot be negative")
            cleaned[k.strip()] = float(val)
        return cleaned

    def dict(self) -> Dict[str, float]:  # type: ignore[override]
        return self.root


class FoodTemplateCreate(BaseModel):
    name: str
    category: Optional[str] = None
    base_quantity: float = Field(100, gt=0, description="Base quantity in grams that nutrients map refers to")
    nutrients: NutrientMap
    favorite: bool = False


class FoodTemplateUpdate(BaseModel):
    name: Optional[str] = None
    category: Optional[str] = None
    base_quantity: Optional[float] = Field(None, gt=0)
    nutrients: Optional[NutrientMap] = None
    favorite: Optional[bool] = None


class IntakeCreate(BaseModel):
    template_id: Optional[int] = Field(None, description="Reuse existing food template")
    name: Optional[str] = Field(None, description="Custom name when not using template")
    category: Optional[str] = None
    grams: float = Field(..., gt=0, description="Consumed grams")
    nutrients: Optional[NutrientMap] = Field(
        None, description="Custom nutrient map when not using template"
    )
    nutrients_base_quantity: Optional[float] = Field(
        None, gt=0, description="If nutrients are provided for a non-100g base, specify that base to normalize"
    )
    intake_at: Optional[str] = Field(None, description="ISO timestamp of intake; defaults to now")
    meal_id: Optional[int] = Field(None, description="Attach to an existing meal")


class IntakeUpdate(BaseModel):
    grams: Optional[float] = Field(None, gt=0)
    name: Optional[str] = None
    category: Optional[str] = None
    nutrients: Optional[NutrientMap] = None
    nutrients_base_quantity: Optional[float] = Field(None, gt=0)
    intake_at: Optional[str] = None
    meal_id: Optional[int] = Field(None, description="Attach to an existing meal or null to detach")


class MealItem(BaseModel):
    template_id: Optional[int] = None
    name: Optional[str] = None
    category: Optional[str] = None
    grams: float = Field(..., gt=0)
    nutrients: Optional[NutrientMap] = None
    nutrients_base_quantity: Optional[float] = Field(None, gt=0)


class MealCreate(BaseModel):
    name: Optional[str] = Field(None, description="Meal label, e.g., breakfast")
    meal_time: Optional[str] = Field(None, description="ISO timestamp in local Pacific time; defaults to now")
    items: List[MealItem]
    day_type: Optional[str] = Field(None, description="rest or training")


class WeightCreate(BaseModel):
    value_kg: float = Field(..., gt=0)
    logged_at: Optional[str] = Field(None, description="ISO timestamp in local Pacific time; defaults to now")
    note: Optional[str] = None


class WeightUpdate(BaseModel):
    value_kg: Optional[float] = Field(None, gt=0)
    logged_at: Optional[str] = None
    note: Optional[str] = None


class ProfilePayload(BaseModel):
    height_cm: Optional[float] = Field(None, gt=0)
    weight_kg: Optional[float] = Field(None, gt=0)
    age: Optional[int] = Field(None, gt=0)
    sex: Optional[str] = Field(None, description="male or female")

    @field_validator("sex")
    @classmethod
    def validate_sex(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        v = v.lower()
        if v not in {"male", "female"}:
            raise ValueError("sex must be 'male' or 'female'")
        return v


class GoalItem(BaseModel):
    nutrient: str
    min_per_day: Optional[float] = Field(None, ge=0)
    max_per_day: Optional[float] = Field(None, ge=0)
    day_type: str = Field("rest", description="rest or training")

    @field_validator("nutrient")
    @classmethod
    def validate_nutrient(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("nutrient name required")
        return v

    @field_validator("day_type")
    @classmethod
    def validate_day_type(cls, v: str) -> str:
        v = v.lower()
        if v not in DAY_TYPES:
            raise ValueError("day_type must be 'rest' or 'training'")
        return v


# --------- Helpers ---------

def normalize_nutrients(nutrients: Dict[str, float], base_quantity: float) -> Dict[str, float]:
    factor = 100.0 / base_quantity
    return {k: round(v * factor, 4) for k, v in nutrients.items()}


def totals_from_normalized(normalized: Dict[str, float], grams: float) -> Dict[str, float]:
    factor = grams / 100.0
    return {k: round(v * factor, 4) for k, v in normalized.items()}


def resolve_intake(
    conn: sqlite3.Connection,
    template_id: Optional[int],
    name: Optional[str],
    category: Optional[str],
    grams: float,
    nutrients: Optional[Dict[str, float]],
    nutrients_base_quantity: Optional[float],
    intake_at: Optional[str],
) -> Dict:
    normalized: Dict[str, float]
    resolved_name = name
    resolved_category = category
    if template_id:
        tpl = fetch_template(conn, template_id)
        raw = json.loads(tpl["nutrients_json"])
        normalized = normalize_nutrients(raw, tpl["base_quantity"])
        resolved_category = resolved_category or tpl["category"]
        resolved_name = resolved_name or tpl["name"]
    elif nutrients:
        base = nutrients_base_quantity or 100.0
        normalized = normalize_nutrients(nutrients, base)
        if not resolved_name:
            raise HTTPException(status_code=400, detail="name is required when not using a template")
    else:
        raise HTTPException(status_code=400, detail="Provide template_id or nutrients")

    intake_dt = parse_iso_date(intake_at) or datetime.datetime.now(LOCAL_TZ)
    intake_at_iso = to_local_iso(intake_dt)

    return {
        "template_id": template_id,
        "name": resolved_name,
        "category": resolved_category,
        "grams": grams,
        "normalized": normalized,
        "intake_at": intake_at_iso,
    }


def insert_intake(
    conn: sqlite3.Connection,
    intake_data: Dict,
    meal_id: Optional[int] = None,
) -> sqlite3.Row:
    now = now_ts()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO food_intakes (template_id, meal_id, name, category, grams, normalized_nutrients_json, intake_at, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            intake_data["template_id"],
            meal_id,
            intake_data["name"],
            intake_data["category"],
            intake_data["grams"],
            json.dumps(intake_data["normalized"]),
            intake_data["intake_at"],
            now,
            now,
        ),
    )
    conn.commit()
    return conn.execute("SELECT * FROM food_intakes WHERE id = ?", (cur.lastrowid,)).fetchone()


def fetch_template(conn: sqlite3.Connection, template_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM food_templates WHERE id = ?", (template_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Food template not found")
    return row


def fetch_meal(conn: sqlite3.Connection, meal_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM meals WHERE id = ?", (meal_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Meal not found")
    return row


def normalize_day_type(day_type: Optional[str]) -> str:
    if day_type is None:
        return "rest"
    day_type = day_type.lower()
    if day_type not in DAY_TYPES:
        raise HTTPException(status_code=400, detail="day_type must be 'rest' or 'training'")
    return day_type


def upsert_day_type(conn: sqlite3.Connection, day: str, day_type: str) -> None:
    now = now_ts()
    conn.execute(
        """
        INSERT INTO day_types (date, day_type, created_at, updated_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(date) DO UPDATE SET day_type = excluded.day_type, updated_at = excluded.updated_at
        """,
        (day, day_type, now, now),
    )
    conn.commit()


def fetch_day_types(conn: sqlite3.Connection, start: datetime.date, end: datetime.date) -> Dict[str, str]:
    rows = conn.execute(
        "SELECT date, day_type FROM day_types WHERE date BETWEEN ? AND ?",
        (start.isoformat(), end.isoformat()),
    ).fetchall()
    mapping = {row["date"]: row["day_type"] for row in rows}
    # Fill missing days as rest for the range
    day = start
    while day <= end:
        mapping.setdefault(day.isoformat(), "rest")
        day += datetime.timedelta(days=1)
    return mapping


def parse_iso_date(date_str: Optional[str]) -> Optional[datetime.datetime]:
    if date_str is None:
        return None
    try:
        dt = datetime.datetime.fromisoformat(date_str)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid datetime format; use ISO 8601")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=LOCAL_TZ)
    return dt.astimezone(LOCAL_TZ)


def to_local_iso(dt: datetime.datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=LOCAL_TZ)
    return dt.astimezone(LOCAL_TZ).isoformat()


def row_to_food(row: sqlite3.Row) -> Dict:
    raw = json.loads(row["nutrients_json"])
    base_qty = row["base_quantity"]
    return {
        "id": row["id"],
        "name": row["name"],
        "category": row["category"],
        "base_quantity": base_qty,
        "favorite": bool(row["favorite"]),
        "nutrients_per_base": raw,
        "nutrients_per_100g": normalize_nutrients(raw, base_qty),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def row_to_intake(row: sqlite3.Row) -> Dict:
    normalized = json.loads(row["normalized_nutrients_json"])
    grams = row["grams"]
    return {
        "id": row["id"],
        "template_id": row["template_id"],
        "meal_id": row["meal_id"],
        "name": row["name"],
        "category": row["category"],
        "grams": grams,
        "intake_at": row["intake_at"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "normalized_nutrients_per_100g": normalized,
        "totals": totals_from_normalized(normalized, grams),
    }


def row_to_meal(row: sqlite3.Row, items: Optional[List[sqlite3.Row]] = None) -> Dict:
    payload = {
        "id": row["id"],
        "name": row["name"],
        "day_type": row["day_type"],
        "meal_time": row["meal_time"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }
    if items is not None:
        payload["items"] = [row_to_intake(item) for item in items]
    return payload


def row_to_weight(row: sqlite3.Row) -> Dict:
    return {
        "id": row["id"],
        "value_kg": row["value_kg"],
        "note": row["note"],
        "logged_at": row["logged_at"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def upsert_profile(conn: sqlite3.Connection, payload: ProfilePayload) -> Dict:
    now = now_ts()
    existing = conn.execute("SELECT * FROM user_profile WHERE id = 1").fetchone()
    if existing:
        conn.execute(
            """
            UPDATE user_profile
            SET height_cm = COALESCE(?, height_cm),
                weight_kg = COALESCE(?, weight_kg),
                age = COALESCE(?, age),
                sex = COALESCE(?, sex),
                updated_at = ?
            WHERE id = 1
            """,
            (payload.height_cm, payload.weight_kg, payload.age, payload.sex, now),
        )
    else:
        conn.execute(
            """
            INSERT INTO user_profile (id, height_cm, weight_kg, age, sex, created_at, updated_at)
            VALUES (1, ?, ?, ?, ?, ?, ?)
            """,
            (payload.height_cm, payload.weight_kg, payload.age, payload.sex, now, now),
        )
    conn.commit()
    row = conn.execute("SELECT * FROM user_profile WHERE id = 1").fetchone()
    return row_to_profile(row)


def row_to_profile(row: Optional[sqlite3.Row]) -> Dict:
    if row is None:
        return {"height_cm": None, "weight_kg": None, "age": None, "sex": None, "created_at": None, "updated_at": None}
    return {
        "height_cm": row["height_cm"],
        "weight_kg": row["weight_kg"],
        "age": row["age"],
        "sex": row["sex"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def compute_bmi_bmr(profile: Dict, weight_override: Optional[float] = None) -> Dict:
    height_cm = profile.get("height_cm")
    weight_kg = weight_override if weight_override is not None else profile.get("weight_kg")
    age = profile.get("age")
    sex = profile.get("sex")
    bmi = None
    bmr = None
    if height_cm and weight_kg:
        height_m = height_cm / 100.0
        bmi = round(weight_kg / (height_m ** 2), 2)
    if height_cm and weight_kg and age and sex:
        if sex == "male":
            bmr_val = 10 * weight_kg + 6.25 * height_cm - 5 * age + 5
        else:
            bmr_val = 10 * weight_kg + 6.25 * height_cm - 5 * age - 161
        bmr = round(bmr_val, 2)
    return {"bmi": bmi, "bmr": bmr}


def fetch_latest_weight(conn: sqlite3.Connection) -> Optional[Dict]:
    row = conn.execute(
        "SELECT * FROM weight_logs ORDER BY logged_at DESC LIMIT 1"
    ).fetchone()
    if not row:
        return None
    return row_to_weight(row)


def weight_stats(conn: sqlite3.Connection, start: datetime.datetime, end: datetime.datetime) -> Dict:
    rows = conn.execute(
        "SELECT * FROM weight_logs WHERE logged_at BETWEEN ? AND ? ORDER BY logged_at",
        (to_local_iso(start), to_local_iso(end)),
    ).fetchall()
    if not rows:
        return {"count": 0, "latest": None, "min": None, "max": None, "avg": None, "delta": None}
    values = [row["value_kg"] for row in rows]
    latest = row_to_weight(rows[-1])
    delta = round(values[-1] - values[0], 3) if len(values) > 1 else 0.0
    return {
        "count": len(rows),
        "latest": latest,
        "min": round(min(values), 3),
        "max": round(max(values), 3),
        "avg": round(sum(values) / len(values), 3),
        "delta": delta,
    }


def merge_goals(conn: sqlite3.Connection, goals: List[GoalItem]) -> Dict[str, Dict[str, Dict[str, Optional[float]]]]:
    now = now_ts()
    for goal in goals:
        day_type = normalize_day_type(goal.day_type)
        existing = conn.execute(
            "SELECT id FROM nutrient_goals WHERE nutrient = ? AND day_type = ?",
            (goal.nutrient, day_type),
        ).fetchone()
        if existing:
            conn.execute(
                """
                UPDATE nutrient_goals
                SET min_per_day = ?, max_per_day = ?, updated_at = ?, day_type = ?
                WHERE nutrient = ? AND day_type = ?
                """,
                (goal.min_per_day, goal.max_per_day, now, day_type, goal.nutrient, day_type),
            )
        else:
            conn.execute(
                """
                INSERT INTO nutrient_goals (nutrient, day_type, min_per_day, max_per_day, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (goal.nutrient, day_type, goal.min_per_day, goal.max_per_day, now, now),
            )
    conn.commit()
    return fetch_goals(conn)


def fetch_goals(conn: sqlite3.Connection) -> Dict[str, Dict[str, Dict[str, Optional[float]]]]:
    res: Dict[str, Dict[str, Dict[str, Optional[float]]]] = {dt: {} for dt in DAY_TYPES}
    for row in conn.execute("SELECT nutrient, day_type, min_per_day, max_per_day FROM nutrient_goals"):
        res.setdefault(row["day_type"], {})[row["nutrient"]] = {
            "min_per_day": row["min_per_day"],
            "max_per_day": row["max_per_day"],
        }
    return res


def aggregate_intakes(
    rows: List[sqlite3.Row],
    goals_by_type: Dict[str, Dict[str, Dict[str, Optional[float]]]],
    day_types: Dict[str, str],
    start: datetime.datetime,
    end: datetime.datetime,
) -> Dict:
    nutrient_totals: Dict[str, float] = {}
    category_totals: Dict[str, Dict[str, float]] = {}
    daily_totals: Dict[str, Dict[str, float]] = {}
    active_days: set[str] = set()

    for row in rows:
        normalized = json.loads(row["normalized_nutrients_json"])
        totals = totals_from_normalized(normalized, row["grams"])
        day = row["intake_at"][:10]
        active_days.add(day)
        daily_bucket = daily_totals.setdefault(day, {})
        for nutrient, val in totals.items():
            nutrient_totals[nutrient] = nutrient_totals.get(nutrient, 0.0) + val
            daily_bucket[nutrient] = daily_bucket.get(nutrient, 0.0) + val
        cat = row["category"] or "uncategorized"
        bucket = category_totals.setdefault(cat, {})
        for nutrient, val in totals.items():
            bucket[nutrient] = bucket.get(nutrient, 0.0) + val

    # Average goals per nutrient across the days in range, respecting day_type
    min_sum: Dict[str, float] = {}
    min_count: Dict[str, int] = {}
    max_sum: Dict[str, float] = {}
    max_count: Dict[str, int] = {}

    day_cursor = start.date()
    while day_cursor <= end.date():
        day_str = day_cursor.isoformat()
        day_type = day_types.get(day_str, "rest")
        goal_map = goals_by_type.get(day_type, {})
        for nutrient, goal in goal_map.items():
            if goal.get("min_per_day") is not None:
                min_sum[nutrient] = min_sum.get(nutrient, 0.0) + goal["min_per_day"]  # type: ignore[arg-type]
                min_count[nutrient] = min_count.get(nutrient, 0) + 1
            if goal.get("max_per_day") is not None:
                max_sum[nutrient] = max_sum.get(nutrient, 0.0) + goal["max_per_day"]  # type: ignore[arg-type]
                max_count[nutrient] = max_count.get(nutrient, 0) + 1
        day_cursor += datetime.timedelta(days=1)

    avg_goal_min = {k: min_sum[k] / min_count[k] for k in min_sum}
    avg_goal_max = {k: max_sum[k] / max_count[k] for k in max_sum}

    days_for_avg = max(len(active_days), 1)
    period_days = max((end.date() - start.date()).days + 1, 1)

    nutrient_summary = {}
    for nutrient, total in nutrient_totals.items():
        avg = total / days_for_avg
        min_goal = avg_goal_min.get(nutrient)
        max_goal = avg_goal_max.get(nutrient)
        status = "ok"
        if min_goal is not None and avg < min_goal:
            status = "low"
        if max_goal is not None and avg > max_goal:
            status = "high"
        nutrient_summary[nutrient] = {
            "total": round(total, 3),
            "daily_avg": round(avg, 3),
            "goal": {"min_per_day": min_goal, "max_per_day": max_goal},
            "status": status,
            "percent_of_min": round(avg / min_goal * 100, 1) if min_goal else None,
            "percent_of_max": round(avg / max_goal * 100, 1) if max_goal else None,
        }
    return {
        "period_days": period_days,
        "active_days": len(active_days),
        "totals": nutrient_summary,
        "by_category": {cat: {k: round(v, 3) for k, v in nutrients.items()} for cat, nutrients in category_totals.items()},
        "daily_totals": {day: {k: round(v, 3) for k, v in vals.items()} for day, vals in daily_totals.items()},
    }


def export_csv(rows: List[sqlite3.Row]) -> Tuple[str, List[str]]:
    # Collect nutrient keys to build a flexible header.
    nutrient_keys = set()
    parsed_rows = []
    for row in rows:
        normalized = json.loads(row["normalized_nutrients_json"])
        totals = totals_from_normalized(normalized, row["grams"])
        nutrient_keys.update(totals.keys())
        parsed_rows.append((row, totals))
    nutrient_headers = sorted(nutrient_keys)
    header = ["id", "intake_at", "name", "category", "grams"] + nutrient_headers
    lines = [",".join(header)]
    for row, totals in parsed_rows:
        line = [
            str(row["id"]),
            row["intake_at"],
            row["name"],
            row["category"] or "",
            str(row["grams"]),
        ]
        for key in nutrient_headers:
            line.append(str(totals.get(key, 0)))
        lines.append(",".join(line))
    csv_data = "\n".join(lines)
    return csv_data, header


# --------- Routes ---------
@app.get("/health")
def health() -> Dict[str, str]:
    return {"status": "ok"}


@app.post("/foods")
def create_food(payload: FoodTemplateCreate):
    conn = get_conn()
    now = now_ts()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO food_templates (name, category, base_quantity, nutrients_json, favorite, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            payload.name,
            payload.category,
            payload.base_quantity,
            json.dumps(payload.nutrients.dict()),
            int(payload.favorite),
            now,
            now,
        ),
    )
    conn.commit()
    new_row = conn.execute("SELECT * FROM food_templates WHERE id = ?", (cur.lastrowid,)).fetchone()
    conn.close()
    return row_to_food(new_row)


@app.post("/foods/bulk_import")
def bulk_import_food(templates: List[FoodTemplateCreate]):
    if not templates:
        return []
    conn = get_conn()
    now = now_ts()
    cur = conn.cursor()
    rows = []
    for tpl in templates:
        cur.execute(
            """
            INSERT INTO food_templates (name, category, base_quantity, nutrients_json, favorite, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                tpl.name,
                tpl.category,
                tpl.base_quantity,
                json.dumps(tpl.nutrients.dict()),
                int(tpl.favorite),
                now,
                now,
            ),
        )
        rows.append(cur.lastrowid)
    conn.commit()
    items = conn.execute(
        "SELECT * FROM food_templates WHERE id IN (%s)" % ",".join(["?"] * len(rows)), rows
    ).fetchall()
    conn.close()
    return [row_to_food(r) for r in items]


@app.get("/foods")
def list_foods(favorite: Optional[bool] = Query(None)):
    conn = get_conn()
    if favorite is None:
        rows = conn.execute("SELECT * FROM food_templates ORDER BY updated_at DESC").fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM food_templates WHERE favorite = ? ORDER BY updated_at DESC",
            (1 if favorite else 0,),
        ).fetchall()
    conn.close()
    return [row_to_food(r) for r in rows]


@app.get("/foods/{template_id}")
def get_food(template_id: int):
    conn = get_conn()
    row = fetch_template(conn, template_id)
    conn.close()
    return row_to_food(row)


@app.put("/foods/{template_id}")
def update_food(template_id: int, payload: FoodTemplateUpdate):
    conn = get_conn()
    row = fetch_template(conn, template_id)
    nutrients_json = row["nutrients_json"]
    base_quantity = row["base_quantity"]
    if payload.base_quantity is not None:
        base_quantity = payload.base_quantity
    if payload.nutrients is not None:
        nutrients_json = json.dumps(payload.nutrients.dict())
    now = now_ts()
    conn.execute(
        """
        UPDATE food_templates
        SET name = COALESCE(?, name),
            category = COALESCE(?, category),
            base_quantity = ?,
            nutrients_json = ?,
            favorite = COALESCE(?, favorite),
            updated_at = ?
        WHERE id = ?
        """,
        (
            payload.name,
            payload.category,
            base_quantity,
            nutrients_json,
            int(payload.favorite) if payload.favorite is not None else None,
            now,
            template_id,
        ),
    )
    conn.commit()
    updated = conn.execute("SELECT * FROM food_templates WHERE id = ?", (template_id,)).fetchone()
    conn.close()
    return row_to_food(updated)


@app.delete("/foods/{template_id}")
def delete_food(template_id: int):
    conn = get_conn()
    fetch_template(conn, template_id)
    conn.execute("DELETE FROM food_templates WHERE id = ?", (template_id,))
    conn.commit()
    conn.close()
    return {"deleted": template_id}


@app.post("/intakes")
def create_intake(payload: IntakeCreate):
    conn = get_conn()
    if payload.meal_id is not None:
        fetch_meal(conn, payload.meal_id)
    intake_data = resolve_intake(
        conn=conn,
        template_id=payload.template_id,
        name=payload.name,
        category=payload.category,
        grams=payload.grams,
        nutrients=payload.nutrients.dict() if payload.nutrients else None,
        nutrients_base_quantity=payload.nutrients_base_quantity,
        intake_at=payload.intake_at,
    )
    new_row = insert_intake(conn, intake_data, meal_id=payload.meal_id)
    conn.close()
    return row_to_intake(new_row)


@app.get("/intakes")
def list_intakes(start: Optional[str] = None, end: Optional[str] = None):
    start_dt = parse_iso_date(start)
    end_dt = parse_iso_date(end)
    conn = get_conn()
    query = "SELECT * FROM food_intakes"
    params: List = []
    if start_dt and end_dt:
        query += " WHERE intake_at BETWEEN ? AND ?"
        params.extend([to_local_iso(start_dt), to_local_iso(end_dt)])
    elif start_dt:
        query += " WHERE intake_at >= ?"
        params.append(to_local_iso(start_dt))
    elif end_dt:
        query += " WHERE intake_at <= ?"
        params.append(to_local_iso(end_dt))
    query += " ORDER BY intake_at DESC"
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return [row_to_intake(r) for r in rows]


@app.get("/intakes/{intake_id}")
def get_intake(intake_id: int):
    conn = get_conn()
    row = conn.execute("SELECT * FROM food_intakes WHERE id = ?", (intake_id,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Intake not found")
    conn.close()
    return row_to_intake(row)


@app.put("/intakes/{intake_id}")
def update_intake(intake_id: int, payload: IntakeUpdate):
    conn = get_conn()
    row = conn.execute("SELECT * FROM food_intakes WHERE id = ?", (intake_id,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Intake not found")

    normalized = json.loads(row["normalized_nutrients_json"])
    grams = row["grams"]
    intake_at = row["intake_at"]
    name = row["name"]
    category = row["category"]
    meal_id = row["meal_id"]

    if payload.grams is not None:
        grams = payload.grams
    if payload.nutrients is not None:
        base = payload.nutrients_base_quantity or 100.0
        normalized = normalize_nutrients(payload.nutrients.dict(), base)
    if payload.intake_at is not None:
        intake_at = to_local_iso(parse_iso_date(payload.intake_at))
    if payload.name is not None:
        name = payload.name
    if payload.category is not None:
        category = payload.category
    if payload.meal_id is not None:
        if payload.meal_id:
            fetch_meal(conn, payload.meal_id)
            meal_id = payload.meal_id
        else:
            meal_id = None

    now = now_ts()
    conn.execute(
        """
        UPDATE food_intakes
        SET grams = ?, normalized_nutrients_json = ?, intake_at = ?, name = ?, category = ?, meal_id = ?, updated_at = ?
        WHERE id = ?
        """,
        (
            grams,
            json.dumps(normalized),
            intake_at,
            name,
            category,
            meal_id,
            now,
            intake_id,
        ),
    )
    conn.commit()
    updated = conn.execute("SELECT * FROM food_intakes WHERE id = ?", (intake_id,)).fetchone()
    conn.close()
    return row_to_intake(updated)


@app.delete("/intakes/{intake_id}")
def delete_intake(intake_id: int):
    conn = get_conn()
    row = conn.execute("SELECT id FROM food_intakes WHERE id = ?", (intake_id,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Intake not found")
    conn.execute("DELETE FROM food_intakes WHERE id = ?", (intake_id,))
    conn.commit()
    conn.close()
    return {"deleted": intake_id}


# --------- Meals ---------
@app.post("/meals")
def create_meal(payload: MealCreate):
    if not payload.items:
        raise HTTPException(status_code=400, detail="items cannot be empty")
    conn = get_conn()
    meal_time_dt = parse_iso_date(payload.meal_time) or datetime.datetime.now(LOCAL_TZ)
    meal_time = to_local_iso(meal_time_dt)
    day_type = normalize_day_type(payload.day_type)
    now = now_ts()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO meals (name, day_type, meal_time, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (payload.name, day_type, meal_time, now, now),
    )
    meal_id = cur.lastrowid
    # Persist the day's type so analytics can use the right goal set.
    upsert_day_type(conn, meal_time_dt.date().isoformat(), day_type)
    for item in payload.items:
        intake_data = resolve_intake(
            conn=conn,
            template_id=item.template_id,
            name=item.name,
            category=item.category,
            grams=item.grams,
            nutrients=item.nutrients.dict() if item.nutrients else None,
            nutrients_base_quantity=item.nutrients_base_quantity,
            intake_at=meal_time,
        )
        insert_intake(conn, intake_data, meal_id=meal_id)
    meal_row = conn.execute("SELECT * FROM meals WHERE id = ?", (meal_id,)).fetchone()
    items = conn.execute("SELECT * FROM food_intakes WHERE meal_id = ? ORDER BY created_at", (meal_id,)).fetchall()
    conn.close()
    return row_to_meal(meal_row, items)


@app.get("/meals")
def list_meals(start: Optional[str] = None, end: Optional[str] = None):
    start_dt = parse_iso_date(start)
    end_dt = parse_iso_date(end)
    conn = get_conn()
    query = "SELECT * FROM meals"
    params: List = []
    if start_dt and end_dt:
        query += " WHERE meal_time BETWEEN ? AND ?"
        params.extend([to_local_iso(start_dt), to_local_iso(end_dt)])
    elif start_dt:
        query += " WHERE meal_time >= ?"
        params.append(to_local_iso(start_dt))
    elif end_dt:
        query += " WHERE meal_time <= ?"
        params.append(to_local_iso(end_dt))
    query += " ORDER BY meal_time DESC"
    meals = conn.execute(query, params).fetchall()
    result = []
    for meal in meals:
        items = conn.execute("SELECT * FROM food_intakes WHERE meal_id = ? ORDER BY created_at", (meal["id"],)).fetchall()
        result.append(row_to_meal(meal, items))
    conn.close()
    return result


@app.get("/meals/{meal_id}")
def get_meal(meal_id: int):
    conn = get_conn()
    meal = fetch_meal(conn, meal_id)
    items = conn.execute("SELECT * FROM food_intakes WHERE meal_id = ? ORDER BY created_at", (meal_id,)).fetchall()
    conn.close()
    return row_to_meal(meal, items)


@app.delete("/meals/{meal_id}")
def delete_meal(meal_id: int):
    conn = get_conn()
    fetch_meal(conn, meal_id)
    conn.execute("DELETE FROM food_intakes WHERE meal_id = ?", (meal_id,))
    conn.execute("DELETE FROM meals WHERE id = ?", (meal_id,))
    conn.commit()
    conn.close()
    return {"deleted": meal_id, "intakes_deleted": True}


# --------- Weight Logs ---------
@app.post("/weights")
def create_weight(payload: WeightCreate):
    conn = get_conn()
    logged_at = to_local_iso(parse_iso_date(payload.logged_at) or datetime.datetime.now(LOCAL_TZ))
    now = now_ts()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO weight_logs (value_kg, note, logged_at, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (payload.value_kg, payload.note, logged_at, now, now),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM weight_logs WHERE id = ?", (cur.lastrowid,)).fetchone()
    conn.close()
    return row_to_weight(row)


@app.get("/weights")
def list_weights(start: Optional[str] = None, end: Optional[str] = None):
    start_dt = parse_iso_date(start) or datetime.datetime.now(LOCAL_TZ) - datetime.timedelta(days=30)
    end_dt = parse_iso_date(end) or datetime.datetime.now(LOCAL_TZ)
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM weight_logs WHERE logged_at BETWEEN ? AND ? ORDER BY logged_at DESC",
        (to_local_iso(start_dt), to_local_iso(end_dt)),
    ).fetchall()
    conn.close()
    return [row_to_weight(r) for r in rows]


@app.put("/weights/{weight_id}")
def update_weight(weight_id: int, payload: WeightUpdate):
    conn = get_conn()
    row = conn.execute("SELECT * FROM weight_logs WHERE id = ?", (weight_id,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Weight entry not found")
    value_kg = payload.value_kg if payload.value_kg is not None else row["value_kg"]
    note = payload.note if payload.note is not None else row["note"]
    logged_at = row["logged_at"]
    if payload.logged_at is not None:
        logged_at = to_local_iso(parse_iso_date(payload.logged_at))
    now = now_ts()
    conn.execute(
        """
        UPDATE weight_logs
        SET value_kg = ?, note = ?, logged_at = ?, updated_at = ?
        WHERE id = ?
        """,
        (value_kg, note, logged_at, now, weight_id),
    )
    conn.commit()
    updated = conn.execute("SELECT * FROM weight_logs WHERE id = ?", (weight_id,)).fetchone()
    conn.close()
    return row_to_weight(updated)


@app.delete("/weights/{weight_id}")
def delete_weight(weight_id: int):
    conn = get_conn()
    row = conn.execute("SELECT id FROM weight_logs WHERE id = ?", (weight_id,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Weight entry not found")
    conn.execute("DELETE FROM weight_logs WHERE id = ?", (weight_id,))
    conn.commit()
    conn.close()
    return {"deleted": weight_id}


@app.get("/weights/summary")
def weight_summary(start: Optional[str] = None, end: Optional[str] = None):
    start_dt = parse_iso_date(start) or datetime.datetime.now(LOCAL_TZ) - datetime.timedelta(days=30)
    end_dt = parse_iso_date(end) or datetime.datetime.now(LOCAL_TZ)
    conn = get_conn()
    stats = weight_stats(conn, start_dt, end_dt)
    conn.close()
    return stats


@app.get("/user/profile")
def get_profile():
    conn = get_conn()
    row = conn.execute("SELECT * FROM user_profile WHERE id = 1").fetchone()
    profile = row_to_profile(row)
    latest_weight = fetch_latest_weight(conn)
    effective_weight = latest_weight["value_kg"] if latest_weight else None
    metrics = compute_bmi_bmr(profile, weight_override=effective_weight)
    conn.close()
    return {"profile": profile, "metrics": metrics, "latest_weight": latest_weight}


@app.put("/user/profile")
def update_profile(payload: ProfilePayload):
    conn = get_conn()
    profile = upsert_profile(conn, payload)
    latest_weight = fetch_latest_weight(conn)
    effective_weight = latest_weight["value_kg"] if latest_weight else None
    metrics = compute_bmi_bmr(profile, weight_override=effective_weight)
    conn.close()
    return {"profile": profile, "metrics": metrics, "latest_weight": latest_weight}


@app.get("/user/goals")
def get_goals():
    conn = get_conn()
    goals = fetch_goals(conn)
    conn.close()
    return goals


@app.put("/user/goals")
def update_goals(goals: List[GoalItem]):
    conn = get_conn()
    stored = merge_goals(conn, goals)
    conn.close()
    return stored


@app.get("/analytics/summary")
def analytics_summary(period: str = Query("week", pattern="^(day|week|month)$"), start: Optional[str] = None, end: Optional[str] = None):
    now_local = datetime.datetime.now(LOCAL_TZ)
    if start:
        start_dt = parse_iso_date(start)
    else:
        if period == "day":
            start_dt = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
        else:
            delta = datetime.timedelta(days=6 if period == "week" else 29)
            start_dt = now_local - delta
    if end:
        end_dt = parse_iso_date(end)
    else:
        end_dt = now_local

    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM food_intakes WHERE intake_at BETWEEN ? AND ? ORDER BY intake_at",
        (to_local_iso(start_dt), to_local_iso(end_dt)),
    ).fetchall()
    goals = fetch_goals(conn)
    day_types = fetch_day_types(conn, start_dt.date(), end_dt.date())
    summary = aggregate_intakes(rows, goals, day_types, start_dt, end_dt)
    profile = row_to_profile(conn.execute("SELECT * FROM user_profile WHERE id = 1").fetchone())
    latest_weight = fetch_latest_weight(conn)
    effective_weight = latest_weight["value_kg"] if latest_weight else None
    metrics = compute_bmi_bmr(profile, weight_override=effective_weight)
    weights = weight_stats(conn, start_dt, end_dt)
    conn.close()
    return {
        "period": {"start": to_local_iso(start_dt), "end": to_local_iso(end_dt), "type": period},
        "summary": summary,
        "profile_metrics": metrics,
        "sample_size": len(rows),
        "day_types": day_types,
        "weight_stats": weights,
    }


@app.get("/analytics/export")
def analytics_export(format: str = Query("csv", pattern="^(csv|excel)$"), start: Optional[str] = None, end: Optional[str] = None):
    start_dt = parse_iso_date(start) or datetime.datetime.now(LOCAL_TZ) - datetime.timedelta(days=29)
    end_dt = parse_iso_date(end) or datetime.datetime.now(LOCAL_TZ)
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM food_intakes WHERE intake_at BETWEEN ? AND ? ORDER BY intake_at",
        (to_local_iso(start_dt), to_local_iso(end_dt)),
    ).fetchall()
    csv_data, header = export_csv(rows)
    conn.close()
    media_type = "text/csv"
    if format == "excel":
        media_type = "application/vnd.ms-excel"
    filename = f"intakes_{start_dt.date()}_{end_dt.date()}.{ 'csv' if format == 'csv' else 'xls' }"
    return Response(
        content=csv_data,
        media_type=media_type,
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@app.get("/analytics/daily-breakdown")
def daily_breakdown(start: Optional[str] = None, end: Optional[str] = None):
    start_dt = parse_iso_date(start) or datetime.datetime.now(LOCAL_TZ) - datetime.timedelta(days=6)
    end_dt = parse_iso_date(end) or datetime.datetime.now(LOCAL_TZ)
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM food_intakes WHERE intake_at BETWEEN ? AND ? ORDER BY intake_at",
        (to_local_iso(start_dt), to_local_iso(end_dt)),
    ).fetchall()
    breakdown = {}
    for row in rows:
        day = row["intake_at"][:10]
        normalized = json.loads(row["normalized_nutrients_json"])
        totals = totals_from_normalized(normalized, row["grams"])
        bucket = breakdown.setdefault(day, {})
        for k, v in totals.items():
            bucket[k] = bucket.get(k, 0.0) + v
    conn.close()
    return {"start": to_local_iso(start_dt), "end": to_local_iso(end_dt), "days": breakdown}
