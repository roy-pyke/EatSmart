import datetime
import json
import sqlite3
from typing import Dict, List, Optional, Tuple

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field, RootModel, field_validator

DB_PATH = "eatsmart.db"

app = FastAPI(title="EatSmart API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def now_ts() -> str:
    return datetime.datetime.utcnow().isoformat()


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
        CREATE TABLE IF NOT EXISTS food_intakes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            template_id INTEGER,
            name TEXT NOT NULL,
            category TEXT,
            grams REAL NOT NULL,
            normalized_nutrients_json TEXT NOT NULL,
            intake_at TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(template_id) REFERENCES food_templates(id) ON DELETE SET NULL
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
            nutrient TEXT NOT NULL UNIQUE,
            min_per_day REAL,
            max_per_day REAL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
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


class IntakeUpdate(BaseModel):
    grams: Optional[float] = Field(None, gt=0)
    name: Optional[str] = None
    category: Optional[str] = None
    nutrients: Optional[NutrientMap] = None
    nutrients_base_quantity: Optional[float] = Field(None, gt=0)
    intake_at: Optional[str] = None


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

    @field_validator("nutrient")
    @classmethod
    def validate_nutrient(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("nutrient name required")
        return v


# --------- Helpers ---------

def normalize_nutrients(nutrients: Dict[str, float], base_quantity: float) -> Dict[str, float]:
    factor = 100.0 / base_quantity
    return {k: round(v * factor, 4) for k, v in nutrients.items()}


def totals_from_normalized(normalized: Dict[str, float], grams: float) -> Dict[str, float]:
    factor = grams / 100.0
    return {k: round(v * factor, 4) for k, v in normalized.items()}


def fetch_template(conn: sqlite3.Connection, template_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM food_templates WHERE id = ?", (template_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Food template not found")
    return row


def parse_iso_date(date_str: Optional[str]) -> Optional[datetime.datetime]:
    if date_str is None:
        return None
    try:
        return datetime.datetime.fromisoformat(date_str)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid datetime format; use ISO 8601")


def row_to_food(row: sqlite3.Row) -> Dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "category": row["category"],
        "base_quantity": row["base_quantity"],
        "favorite": bool(row["favorite"]),
        "nutrients_per_100g": json.loads(row["nutrients_json"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def row_to_intake(row: sqlite3.Row) -> Dict:
    normalized = json.loads(row["normalized_nutrients_json"])
    grams = row["grams"]
    return {
        "id": row["id"],
        "template_id": row["template_id"],
        "name": row["name"],
        "category": row["category"],
        "grams": grams,
        "intake_at": row["intake_at"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "normalized_nutrients_per_100g": normalized,
        "totals": totals_from_normalized(normalized, grams),
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


def compute_bmi_bmr(profile: Dict) -> Dict:
    height_cm = profile.get("height_cm")
    weight_kg = profile.get("weight_kg")
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


def merge_goals(conn: sqlite3.Connection, goals: List[GoalItem]) -> List[Dict]:
    now = now_ts()
    rows = []
    for goal in goals:
        existing = conn.execute(
            "SELECT * FROM nutrient_goals WHERE nutrient = ?", (goal.nutrient,)
        ).fetchone()
        if existing:
            conn.execute(
                """
                UPDATE nutrient_goals
                SET min_per_day = ?, max_per_day = ?, updated_at = ?
                WHERE nutrient = ?
                """,
                (goal.min_per_day, goal.max_per_day, now, goal.nutrient),
            )
        else:
            conn.execute(
                """
                INSERT INTO nutrient_goals (nutrient, min_per_day, max_per_day, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (goal.nutrient, goal.min_per_day, goal.max_per_day, now, now),
            ) 
        rows.append(goal.nutrient)
    conn.commit()
    if not rows:
        return []
    stored = conn.execute(
        "SELECT nutrient, min_per_day, max_per_day, created_at, updated_at FROM nutrient_goals WHERE nutrient IN (%s)" % ",".join(
            ["?"] * len(rows)
        ),
        rows,
    ).fetchall()
    return [dict(r) for r in stored]


def fetch_goals(conn: sqlite3.Connection) -> Dict[str, Dict[str, Optional[float]]]:
    res = {}
    for row in conn.execute("SELECT nutrient, min_per_day, max_per_day FROM nutrient_goals"):
        res[row["nutrient"]] = {"min_per_day": row["min_per_day"], "max_per_day": row["max_per_day"]}
    return res


def aggregate_intakes(rows: List[sqlite3.Row], goals: Dict[str, Dict[str, Optional[float]]], start: datetime.datetime, end: datetime.datetime) -> Dict:
    nutrient_totals: Dict[str, float] = {}
    category_totals: Dict[str, Dict[str, float]] = {}
    days = max((end.date() - start.date()).days + 1, 1)
    for row in rows:
        normalized = json.loads(row["normalized_nutrients_json"])
        totals = totals_from_normalized(normalized, row["grams"])
        for nutrient, val in totals.items():
            nutrient_totals[nutrient] = nutrient_totals.get(nutrient, 0.0) + val
        cat = row["category"] or "uncategorized"
        bucket = category_totals.setdefault(cat, {})
        for nutrient, val in totals.items():
            bucket[nutrient] = bucket.get(nutrient, 0.0) + val
    nutrient_summary = {}
    for nutrient, total in nutrient_totals.items():
        avg = total / days
        goal = goals.get(nutrient, {})
        min_goal = goal.get("min_per_day")
        max_goal = goal.get("max_per_day")
        status = "ok"
        if min_goal is not None and avg < min_goal:
            status = "low"
        if max_goal is not None and avg > max_goal:
            status = "high"
        nutrient_summary[nutrient] = {
            "total": round(total, 3),
            "daily_avg": round(avg, 3),
            "goal": goal,
            "status": status,
            "percent_of_min": round(avg / min_goal * 100, 1) if min_goal else None,
            "percent_of_max": round(avg / max_goal * 100, 1) if max_goal else None,
        }
    return {
        "period_days": days,
        "totals": nutrient_summary,
        "by_category": {cat: {k: round(v, 3) for k, v in nutrients.items()} for cat, nutrients in category_totals.items()},
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
    normalized = normalize_nutrients(payload.nutrients.dict(), payload.base_quantity)
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
            json.dumps(normalized),
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
        normalized = normalize_nutrients(tpl.nutrients.dict(), tpl.base_quantity)
        cur.execute(
            """
            INSERT INTO food_templates (name, category, base_quantity, nutrients_json, favorite, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                tpl.name,
                tpl.category,
                tpl.base_quantity,
                json.dumps(normalized),
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
        nutrients_json = json.dumps(normalize_nutrients(payload.nutrients.dict(), base_quantity))
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
    normalized: Dict[str, float]
    category: Optional[str] = payload.category
    name = payload.name
    if payload.template_id:
        tpl = fetch_template(conn, payload.template_id)
        normalized = json.loads(tpl["nutrients_json"])
        category = category or tpl["category"]
        name = name or tpl["name"]
    elif payload.nutrients:
        base = payload.nutrients_base_quantity or 100.0
        normalized = normalize_nutrients(payload.nutrients.dict(), base)
        if not name:
            raise HTTPException(status_code=400, detail="name is required when not using a template")
    else:
        raise HTTPException(status_code=400, detail="Provide template_id or nutrients")

    intake_at = payload.intake_at or now_ts()
    # Validate intake_at
    parse_iso_date(intake_at)

    now = now_ts()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO food_intakes (template_id, name, category, grams, normalized_nutrients_json, intake_at, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            payload.template_id,
            name,
            category,
            payload.grams,
            json.dumps(normalized),
            intake_at,
            now,
            now,
        ),
    )
    conn.commit()
    new_row = conn.execute("SELECT * FROM food_intakes WHERE id = ?", (cur.lastrowid,)).fetchone()
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
        params.extend([start_dt.isoformat(), end_dt.isoformat()])
    elif start_dt:
        query += " WHERE intake_at >= ?"
        params.append(start_dt.isoformat())
    elif end_dt:
        query += " WHERE intake_at <= ?"
        params.append(end_dt.isoformat())
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

    if payload.grams is not None:
        grams = payload.grams
    if payload.nutrients is not None:
        base = payload.nutrients_base_quantity or 100.0
        normalized = normalize_nutrients(payload.nutrients.dict(), base)
    if payload.intake_at is not None:
        parse_iso_date(payload.intake_at)
        intake_at = payload.intake_at
    if payload.name is not None:
        name = payload.name
    if payload.category is not None:
        category = payload.category

    now = now_ts()
    conn.execute(
        """
        UPDATE food_intakes
        SET grams = ?, normalized_nutrients_json = ?, intake_at = ?, name = ?, category = ?, updated_at = ?
        WHERE id = ?
        """,
        (
            grams,
            json.dumps(normalized),
            intake_at,
            name,
            category,
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


@app.get("/user/profile")
def get_profile():
    conn = get_conn()
    row = conn.execute("SELECT * FROM user_profile WHERE id = 1").fetchone()
    profile = row_to_profile(row)
    metrics = compute_bmi_bmr(profile)
    conn.close()
    return {"profile": profile, "metrics": metrics}


@app.put("/user/profile")
def update_profile(payload: ProfilePayload):
    conn = get_conn()
    profile = upsert_profile(conn, payload)
    metrics = compute_bmi_bmr(profile)
    conn.close()
    return {"profile": profile, "metrics": metrics}


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
def analytics_summary(period: str = Query("week", pattern="^(week|month)$"), start: Optional[str] = None, end: Optional[str] = None):
    today = datetime.datetime.utcnow()
    if start:
        start_dt = parse_iso_date(start)
    else:
        delta = datetime.timedelta(days=6 if period == "week" else 29)
        start_dt = today - delta
    if end:
        end_dt = parse_iso_date(end)
    else:
        end_dt = today

    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM food_intakes WHERE intake_at BETWEEN ? AND ? ORDER BY intake_at",
        (start_dt.isoformat(), end_dt.isoformat()),
    ).fetchall()
    goals = fetch_goals(conn)
    summary = aggregate_intakes(rows, goals, start_dt, end_dt)
    profile = row_to_profile(conn.execute("SELECT * FROM user_profile WHERE id = 1").fetchone())
    metrics = compute_bmi_bmr(profile)
    conn.close()
    return {
        "period": {"start": start_dt.isoformat(), "end": end_dt.isoformat(), "type": period},
        "summary": summary,
        "profile_metrics": metrics,
        "sample_size": len(rows),
    }


@app.get("/analytics/export")
def analytics_export(format: str = Query("csv", pattern="^(csv|excel)$"), start: Optional[str] = None, end: Optional[str] = None):
    start_dt = parse_iso_date(start) or datetime.datetime.utcnow() - datetime.timedelta(days=29)
    end_dt = parse_iso_date(end) or datetime.datetime.utcnow()
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM food_intakes WHERE intake_at BETWEEN ? AND ? ORDER BY intake_at",
        (start_dt.isoformat(), end_dt.isoformat()),
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
    start_dt = parse_iso_date(start) or datetime.datetime.utcnow() - datetime.timedelta(days=6)
    end_dt = parse_iso_date(end) or datetime.datetime.utcnow()
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM food_intakes WHERE intake_at BETWEEN ? AND ? ORDER BY intake_at",
        (start_dt.isoformat(), end_dt.isoformat()),
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
    return {"start": start_dt.isoformat(), "end": end_dt.isoformat(), "days": breakdown}
