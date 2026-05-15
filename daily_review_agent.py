import functions_framework
from google.cloud import bigquery
import numpy as np
from datetime import datetime, timezone
import requests
import google.auth.transport.requests
import google.oauth2.id_token

# Configuration
PROJECT_ID = "your id"
BQ_DATASET = "restaurant_reviews"
BQ_REVIEWS = f"{PROJECT_ID}.{BQ_DATASET}.reviews_raw"
BQ_SCORES = f"{PROJECT_ID}.{BQ_DATASET}.monthly_scores"
BQ_CREDIT = f"{PROJECT_ID}.{BQ_DATASET}.credit_status"
BQ_AUDIT = f"{PROJECT_ID}.{BQ_DATASET}.audit_logs"


AGENT_2_URL = "https://evaluator-agent-114625561414.us-west1.run.app"
bq = bigquery.Client(project=PROJECT_ID)

def refresh_monthly_scores():
    """
    Aggregates reviews_raw into monthly_scores.
    Uses WRITE_TRUNCATE to ensure data is always fresh.
    """
    query = f"""
    SELECT
        business_name,
        DATE_TRUNC(CAST(review_date AS DATE), MONTH) AS month,
        ROUND(AVG(converted_score), 4) AS avg_score,
        COUNT(*) AS review_count,
        CURRENT_TIMESTAMP() as updated_at
    FROM `{BQ_REVIEWS}`
    WHERE converted_score IS NOT NULL
    GROUP BY business_name, month
    """
    df = bq.query(query).to_dataframe()
    
    # Overwrite the table completely with new calculations
    job_config = bigquery.LoadJobConfig(write_disposition="WRITE_TRUNCATE")
    job = bq.load_table_from_dataframe(df, BQ_SCORES, job_config=job_config)
    job.result() # Wait for completion
    print("✅ Monthly scores refreshed.")

@functions_framework.http
def daily_agent(request):
    # 1. Refresh aggregates before analysis
    try:
        refresh_monthly_scores()
    except Exception as e:
        return f"Error refreshing scores: {str(e)}", 500

    # 2. Get list of businesses
    query_biz = f"SELECT DISTINCT business_name FROM `{BQ_SCORES}`"
    businesses = [r.business_name for r in bq.query(query_biz).result()]
    processed_businesses = []

    for biz in businesses:
        # Get scores for this business
        query_scores = f"""
            SELECT month, avg_score AS Converted_Score 
            FROM `{BQ_SCORES}` 
            WHERE business_name = @biz 
            ORDER BY month ASC
        """
        job_config = bigquery.QueryJobConfig(query_parameters=[bigquery.ScalarQueryParameter("biz", "STRING", biz)])
        df = bq.query(query_scores, job_config=job_config).to_dataframe()

        scores = df["Converted_Score"].tolist()
        if not scores: continue

        # Calculate Trend
        w = min(3, len(scores))
        recent_avg = float(np.mean(scores[-w:]))
        prior_slice = scores[max(0, len(scores)-w*2) : len(scores)-w]
        prior_avg = float(np.mean(prior_slice)) if prior_slice else recent_avg
        delta = recent_avg - prior_avg
        decline_pct = abs(delta / prior_avg * 100) if prior_avg != 0 else 0

        if recent_avg < -20.0 and decline_pct >= 15.0: trend_status = "ALERT"
        elif delta < 0 and decline_pct >= 7.5: trend_status = "WATCH"
        else: trend_status = "OK"

        summary = f"{trend_status} | Latest: {scores[-1]:.1f} | Trend: {delta:+.1f} pts"

        # 3. Check current Credit Status
        query_credit = f"SELECT credit_status FROM `{BQ_CREDIT}` WHERE business_name = @biz ORDER BY updated_at DESC LIMIT 1"
        rows = list(bq.query(query_credit, job_config=job_config).result())
        current_status = rows[0].credit_status if rows else "ACTIVE"

        # 4. Idempotent Credit Logic (Only write if status changes)
        new_status = current_status
        if trend_status == "ALERT" and current_status != "FROZEN":
            new_status = "FROZEN"
            bq.insert_rows_json(BQ_CREDIT, [{
                "business_name": biz,
                "credit_status": new_status,
                "frozen_reason": f"Auto: {summary}",
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }])

        # 5. Audit Log
        bq.insert_rows_json(BQ_AUDIT, [{
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "business_name": biz,
            "trend_status": trend_status,
            "credit_action": new_status,
            "agent_summary": summary
        }])
        
        processed_businesses.append(biz)

    # 6. Handoff to Agent 2
    if processed_businesses:
        auth_req = google.auth.transport.requests.Request()
        id_token = google.oauth2.id_token.fetch_id_token(auth_req, AGENT_2_URL)
        headers = {"Authorization": f"Bearer {id_token}", "Content-Type": "application/json"}
        payload = {"businesses": processed_businesses}

        try:
            response = requests.post(AGENT_2_URL, json=payload, headers=headers)
            evaluator_status = f"Agent 2 responded: {response.text}"
        except Exception as e:
            evaluator_status = f"Failed to call Agent 2: {str(e)}"
    else:
        evaluator_status = "No businesses processed."

    return f"Agent 1 Complete. {evaluator_status}", 200