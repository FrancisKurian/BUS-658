import functions_framework
from google.cloud import storage, bigquery
from transformers import pipeline as hf_pipeline
import json
import requests
import google.auth.transport.requests
import google.oauth2.id_token
from datetime import datetime, timezone

# Configuration
PROJECT_ID = "your id"
BUCKET_NAME = "review-agent-data-493800"
JSON_FILE = "business_reviews.json"
AGENT_1_URL = "https://daily-review-agent-114625561414.us-west1.run.app"

# Initialize Clients
storage_client = storage.Client()
bq_client = bigquery.Client(project=PROJECT_ID)
classifier = hf_pipeline("sentiment-analysis", model="nlptown/bert-base-multilingual-uncased-sentiment")

@functions_framework.http
def ingest_from_gcs(request):
    # 1. Read JSON from GCS
    bucket = storage_client.bucket(BUCKET_NAME)
    blob = bucket.blob(JSON_FILE)
    data = json.loads(blob.download_as_text())

    # 2. Score and Prepare Rows
    rows_to_insert = []
    for business in data:
        biz_name = business["name"]
        for review in business["reviews"]:
            text = review["review_text"]
            sent = classifier(text[:512])[0]
            label, raw = sent["label"], sent["score"]
            converted = raw * 100 if label not in ("1 stars", "2 stars") else raw * -100

            rows_to_insert.append({
                "business_name": biz_name,
                "review_text": text,
                "review_date": review["date"],
                "sentiment_label": label,
                "converted_score": round(converted, 4),
                "scored_at": datetime.now(timezone.utc).isoformat(),
            })

    # 3. Write to BigQuery
    table_id = f"{PROJECT_ID}.restaurant_reviews.reviews_raw"
    errors = bq_client.insert_rows_json(table_id, rows_to_insert)
    
    if errors: return f"Error: {errors}", 500

    # 4. Trigger next Agent
    auth_req = google.auth.transport.requests.Request()
    id_token = google.oauth2.id_token.fetch_id_token(auth_req, AGENT_1_URL)
    headers = {"Authorization": f"Bearer {id_token}"}
    requests.post(AGENT_1_URL, headers=headers)

    return "Ingestion complete. Agent 1 triggered.", 200

