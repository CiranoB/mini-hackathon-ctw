from __future__ import annotations

import logging
import os
import time
from io import BytesIO

import boto3
import pyarrow.parquet as pq
from fastapi import FastAPI, HTTPException, Query, Request

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("api.timing")


def _get_s3_client():
    return boto3.client(
        "s3",
        endpoint_url=os.getenv("AWS_ENDPOINT_URL", "http://localhost:4566"),
        region_name=os.getenv("AWS_REGION", "us-east-1"),
        aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID", "test"),
        aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY", "test"),
    )


def _get_athena_client():
    return boto3.client(
        "athena",
        endpoint_url=os.getenv("AWS_ENDPOINT_URL", "http://localhost:4566"),
        region_name=os.getenv("AWS_REGION", "us-east-1"),
        aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID", "test"),
        aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY", "test"),
    )


def _run_athena_query(sql: str) -> list[dict]:
    athena = _get_athena_client()
    output_location = os.getenv("ATHENA_OUTPUT_LOCATION", "s3://athena-results/")

    execution = athena.start_query_execution(
        QueryString=sql,
        ResultConfiguration={"OutputLocation": output_location},
    )
    query_id = execution["QueryExecutionId"]

    deadline = time.monotonic() + float(os.getenv("ATHENA_TIMEOUT_SECONDS", "30"))
    while True:
        status = athena.get_query_execution(QueryExecutionId=query_id)[
            "QueryExecution"
        ]["Status"]
        state = status["State"]
        if state in ("SUCCEEDED", "FAILED", "CANCELLED"):
            break
        if time.monotonic() > deadline:
            raise HTTPException(status_code=504, detail="Athena query timed out")
        time.sleep(0.1)

    if state != "SUCCEEDED":
        reason = status.get("StateChangeReason", "Unknown error")
        raise HTTPException(status_code=400, detail=f"Athena query {state}: {reason}")

    result = athena.get_query_results(QueryExecutionId=query_id)
    rows = result["ResultSet"]["Rows"]
    if not rows:
        return []

    columns = [col.get("VarCharValue") for col in rows[0]["Data"]]
    return [
        {
            columns[i]: cell.get("VarCharValue")
            for i, cell in enumerate(row["Data"])
        }
        for row in rows[1:]
    ]


def _list_parquet_keys(bucket: str, prefix: str) -> list[str]:
    response = _get_s3_client().list_objects_v2(Bucket=bucket, Prefix=prefix)
    return sorted(
        item["Key"]
        for item in response.get("Contents", [])
        if item["Key"].endswith(".parquet")
    )


def _read_parquet_rows(bucket: str, key: str) -> list[dict]:
    response = _get_s3_client().get_object(Bucket=bucket, Key=key)
    payload = response["Body"].read()
    table = pq.read_table(BytesIO(payload))
    return table.to_pylist()


app = FastAPI(title="Vehicle Data API")


@app.middleware("http")
async def add_timing(request: Request, call_next):
    start = time.perf_counter()
    response = await call_next(request)
    elapsed_ms = (time.perf_counter() - start) * 1000
    response.headers["X-Process-Time-Ms"] = f"{elapsed_ms:.2f}"
    query = f"?{request.url.query}" if request.url.query else ""
    logger.info(
        "%s %s%s -> %s in %.2f ms",
        request.method,
        request.url.path,
        query,
        response.status_code,
        elapsed_ms,
    )
    return response


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/get_all")
def get_all() -> dict[str, list[dict]]:
    bucket = os.getenv("VEHICLE_DATA_BUCKET", "vehicle-data")
    prefix = os.getenv("VEHICLE_DATA_PREFIX", "parquet/")
    keys = _list_parquet_keys(bucket, prefix)

    if not keys:
        raise HTTPException(status_code=404, detail="No parquet files found")

    return {
        key.removeprefix(prefix).removesuffix(".parquet"): _read_parquet_rows(bucket, key)
        for key in keys
    }


@app.get("/athena")
def athena_query(
    sql: str = Query(
        default="SELECT 1 + 1 AS result",
        description="SQL statement to execute via Athena (DuckDB engine).",
    ),
) -> dict[str, object]:
    rows = _run_athena_query(sql)
    return {"query": sql, "row_count": len(rows), "rows": rows}


@app.get("/models")
def models_by_manufacturer(
    manufacturer_id: int = Query(
        description="Manufacturer ID to list all models for.",
    ),
) -> dict[str, object]:
    bucket = os.getenv("VEHICLE_DATA_BUCKET", "vehicle-data")
    prefix = os.getenv("VEHICLE_DATA_PREFIX", "parquet/")
    sql = (
        "SELECT model_id, manufacturer_id, name, segment "
        f"FROM read_parquet('s3://{bucket}/{prefix}models.parquet') "
        f"WHERE manufacturer_id = {manufacturer_id} "
        "ORDER BY model_id"
    )
    rows = _run_athena_query(sql)
    return {"manufacturer_id": manufacturer_id, "row_count": len(rows), "rows": rows}


def _sql_str(value: str) -> str:
    """Escape a string literal for safe inclusion in a SQL query."""
    return "'" + value.replace("'", "''") + "'"


def _t(bucket: str, prefix: str, table: str) -> str:
    """Build a read_parquet() reference to a table's parquet file."""
    return f"read_parquet('s3://{bucket}/{prefix}{table}.parquet')"


def _build_vehicle_summary_sql(
    bucket: str, prefix: str, manufacturer: str, model: str, year: int
) -> str:
    """Build a single heavy query that joins all 10 tables into one summary row.

    The query resolves the target (manufacturer, model, year) tuple, then joins
    every related table — generations, recalls, parts (via model_parts),
    consumers (via consumer_vehicles) and safety_ratings — aggregating each
    branch down to the fields required by the response shape.
    """
    manu = _sql_str(manufacturer)
    mod = _sql_str(model)

    manufacturers = _t(bucket, prefix, "manufacturers")
    models = _t(bucket, prefix, "models")
    model_years = _t(bucket, prefix, "model_years")
    generations = _t(bucket, prefix, "generations")
    recalls = _t(bucket, prefix, "recalls")
    parts = _t(bucket, prefix, "parts")
    model_parts = _t(bucket, prefix, "model_parts")
    consumers = _t(bucket, prefix, "consumers")
    consumer_vehicles = _t(bucket, prefix, "consumer_vehicles")
    safety_ratings = _t(bucket, prefix, "safety_ratings")

    return f"""
WITH base AS (
    SELECT
        m.manufacturer_id,
        m.name AS manufacturer_name,
        m.country AS manufacturer_country,
        m.founded_year,
        md.model_id,
        md.name AS model_name,
        md.segment,
        my.model_year_id,
        my.year,
        my.msrp_usd
    FROM {manufacturers} m
    JOIN {models} md ON md.manufacturer_id = m.manufacturer_id
    JOIN {model_years} my ON my.model_id = md.model_id
    WHERE m.name = {manu} AND md.name = {mod} AND my.year = {int(year)}
    LIMIT 1
),
gen AS (
    SELECT
        g.model_id,
        g.generation_name,
        g.start_year,
        g.end_year
    FROM {generations} g
    JOIN base ON base.model_id = g.model_id
    WHERE {int(year)} BETWEEN g.start_year AND g.end_year
    ORDER BY g.start_year
    LIMIT 1
),
rec AS (
    SELECT
        r.model_year_id,
        COUNT(*) AS recall_count,
        BOOL_OR(NOT r.resolved) AS open_recall
    FROM {recalls} r
    JOIN base ON base.model_year_id = r.model_year_id
    GROUP BY r.model_year_id
),
prt AS (
    SELECT
        mp.model_year_id,
        STRING_AGG(p.part_name, '||' ORDER BY p.part_id) AS parts
    FROM {model_parts} mp
    JOIN base ON base.model_year_id = mp.model_year_id
    JOIN {parts} p ON p.part_id = mp.part_id
    GROUP BY mp.model_year_id
),
owners AS (
    SELECT
        cv.model_year_id,
        c.country
    FROM {consumer_vehicles} cv
    JOIN base ON base.model_year_id = cv.model_year_id
    JOIN {consumers} c ON c.consumer_id = cv.consumer_id
),
cons AS (
    SELECT
        COUNT(*) AS total_owners,
        (
            SELECT o2.country
            FROM owners o2
            GROUP BY o2.country
            ORDER BY COUNT(*) DESC, o2.country
            LIMIT 1
        ) AS top_country
    FROM owners
),
sr AS (
    SELECT
        s.model_year_id,
        s.rating_agency,
        s.overall_rating,
        s.crash_test_score
    FROM {safety_ratings} s
    JOIN base ON base.model_year_id = s.model_year_id
    ORDER BY s.overall_rating DESC
    LIMIT 1
)
SELECT
    base.manufacturer_name,
    base.manufacturer_country,
    base.founded_year,
    base.model_name,
    base.segment,
    base.msrp_usd,
    gen.generation_name,
    gen.start_year,
    gen.end_year,
    COALESCE(rec.recall_count, 0) AS recall_count,
    COALESCE(rec.open_recall, FALSE) AS open_recall,
    prt.parts,
    COALESCE(cons.total_owners, 0) AS total_owners,
    cons.top_country,
    sr.rating_agency,
    sr.overall_rating,
    sr.crash_test_score
FROM base
LEFT JOIN gen ON gen.model_id = base.model_id
LEFT JOIN rec ON rec.model_year_id = base.model_year_id
LEFT JOIN prt ON prt.model_year_id = base.model_year_id
LEFT JOIN cons ON TRUE
LEFT JOIN sr ON sr.model_year_id = base.model_year_id
""".strip()


def _to_int(value: object) -> int | None:
    if value is None or value == "":
        return None
    return int(float(str(value)))


def _to_float(value: object) -> float | None:
    if value is None or value == "":
        return None
    return float(str(value))


def _to_bool(value: object) -> bool:
    return str(value).strip().lower() in ("true", "1", "t")


@app.get("/vehicle-summary")
def vehicle_summary(
    manufacturer: str = Query(description="Manufacturer name, e.g. BMW.", default="BMW"),
    model: str = Query(description="Model name, e.g. X1.", default="X1"),
    year: int = Query(description="Model year, e.g. 1999.", default=1999),
) -> dict[str, object]:
    bucket = os.getenv("VEHICLE_DATA_BUCKET", "vehicle-data")
    prefix = os.getenv("VEHICLE_DATA_PREFIX", "parquet/")

    sql = _build_vehicle_summary_sql(bucket, prefix, manufacturer, model, year)
    rows = _run_athena_query(sql)
    if not rows:
        raise HTTPException(
            status_code=404,
            detail=f"No vehicle found for {manufacturer} {model} {year}",
        )

    row = rows[0]
    parts_raw = row.get("parts") or ""
    parts = [p for p in parts_raw.split("||") if p]

    return {
        "manufacturer": {
            "name": row.get("manufacturer_name"),
            "country": row.get("manufacturer_country"),
            "founded_year": _to_int(row.get("founded_year")),
        },
        "model": {
            "name": row.get("model_name"),
            "segment": row.get("segment"),
            "msrp_usd": _to_int(row.get("msrp_usd")),
        },
        "generation": {
            "name": row.get("generation_name"),
            "start_year": _to_int(row.get("start_year")),
            "end_year": _to_int(row.get("end_year")),
        },
        "recalls": {
            "open_recall": _to_bool(row.get("open_recall")),
            "had_any_recall": (_to_int(row.get("recall_count")) or 0) > 0,
            "recall_count": _to_int(row.get("recall_count")) or 0,
        },
        "parts": parts,
        "consumers": {
            "total_owners": _to_int(row.get("total_owners")) or 0,
            "top_country": row.get("top_country"),
        },
        "safety_rating": {
            "agency": row.get("rating_agency"),
            "overall_rating": _to_float(row.get("overall_rating")),
            "crash_test_score": _to_int(row.get("crash_test_score")),
        },
    }


def main() -> None:
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000, reload=False)


if __name__ == "__main__":
    main()
