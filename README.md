# EatSmart API

FastAPI + SQLite backend for logging foods, nutrient intake, user metrics, and weekly/monthly analysis.

## Quick start
- Python 3.10+  
- Install deps: `pip install -r requirements.txt`
- Run dev server: `uvicorn main:app --reload --port 8000`
- Docs: open `http://localhost:8000/docs`

## Data model
- **Food templates**: name, category, base_quantity (g), nutrients_per_100g (normalized), favorite flag.
- **Food intakes**: grams in g, snapshot of nutrients_per_100g, optional template_id, category, intake_at timestamp.
- **User profile**: height_cm, weight_kg, optional age/sex for BMI/BMR.
- **Nutrient goals**: per-nutrient daily min/max targets.

## Key endpoints (non-exhaustive)
- `POST /foods` / `GET /foods` / `PUT /foods/{id}` / `DELETE /foods/{id}`  
  Create/update templates. Accepts nutrients for any base; server normalizes to per 100g.
- `POST /foods/bulk_import` bulk create templates.
- `POST /intakes` / `GET /intakes` / `PUT /intakes/{id}` / `DELETE /intakes/{id}`  
  Log consumption in grams. If using a template, nutrients auto-filled; otherwise provide nutrients (+ optional base).
- `PUT /user/profile` and `GET /user/profile` (returns BMI/BMR when data available).
- `PUT /user/goals` / `GET /user/goals` for per-day nutrient bounds.
- `GET /analytics/summary?period=week|month`  
  Weekly/monthly totals, daily averages, goal status, category breakdown, BMI/BMR echo.
- `GET /analytics/daily-breakdown` daily nutrient totals.
- `GET /analytics/export?format=csv|excel` export intake records (CSV; Excel served with CSV content-type).

## Notes
- All quantities default to grams; `nutrients_base_quantity` lets you send data for other bases.
- Records carry timestamps to enable time-range queries.
- Favorite templates enable quick reuse via `template_id` when creating intakes.
