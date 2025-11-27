# EatSmart API

FastAPI + SQLite backend for logging foods, nutrient intake, user metrics, and weekly/monthly analysis.

## Quick start
- Python 3.10+  
- Install deps: `pip install -r requirements.txt`
- Run dev server: `uvicorn main:app --reload --port 8000`
- Docs: open `http://localhost:8000/docs`

## Data model
- **Meals**: meal_time (Pacific), name/label, contains multiple intake items.
- **Food templates**: name, category, base_quantity (g as provided), nutrients_per_base (stored as entered), favorite flag. Server computes per-100g on the fly when needed.
- **Food intakes**: grams in g, snapshot of nutrients_per_100g, optional template_id, meal_id, category, intake_at timestamp.
- **Day types**: per-day flag (rest/training) to select the right nutrient goal set; stored when logging a meal.
- **Weight logs**: history of weight readings with timestamps for trend analysis.
- **User profile**: height_cm, weight_kg, optional age/sex for BMI/BMR.
- **Nutrient goals**: per-nutrient daily min/max targets.

## Key endpoints (non-exhaustive)
- `POST /meals` / `GET /meals` / `GET /meals/{id}` / `DELETE /meals/{id}`  
  Create a meal (Pacific time default) with multiple items (template-based or ad-hoc nutrients) and mark the day as rest/training.
- `POST /foods` / `GET /foods` / `PUT /foods/{id}` / `DELETE /foods/{id}`  
  Create/update templates. Accepts nutrients for any base; server normalizes to per 100g.
- `POST /foods/bulk_import` bulk create templates.
- `POST /intakes` / `GET /intakes` / `PUT /intakes/{id}` / `DELETE /intakes/{id}`  
  Log consumption in grams. If using a template, nutrients auto-filled; otherwise provide nutrients (+ optional base).
- `PUT /user/profile` and `GET /user/profile` (returns BMI/BMR when data available).
- `PUT /user/goals` / `GET /user/goals` for per-day nutrient bounds, separated by rest/training.
- `POST/GET/PUT/DELETE /weights` and `GET /weights/summary` for weight history CRUD + stats.
- `GET /analytics/summary?period=week|month`  
  Weekly/monthly totals, daily averages, goal status, category breakdown, BMI/BMR echo.
- `GET /analytics/daily-breakdown` daily nutrient totals.
- `GET /analytics/export?format=csv|excel` export intake records (CSV; Excel served with CSV content-type).

## Notes
- All quantities default to grams; `nutrients_base_quantity` lets you send data for other bases.
- Records carry timestamps to enable time-range queries.
- Favorite templates enable quick reuse via `template_id` when creating intakes.
- All timestamps default to America/Los_Angeles local time; ISO inputs without tz are treated as Pacific.
- Day type (rest/training) drives which nutrient goals are applied per day in analytics.
