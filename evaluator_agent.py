import functions_framework
from google.cloud import bigquery
#import google.generativeai as genai
from datetime import datetime, timezone
# Replace: import google.generativeai as genai
import vertexai
from vertexai.generative_models import GenerativeModel

PROJECT_ID = "your id"
BQ_DATASET = "restaurant_reviews"
BQ_REVIEWS = f"{PROJECT_ID}.{BQ_DATASET}.reviews_raw"
BQ_AUDIT = f"{PROJECT_ID}.{BQ_DATASET}.audit_logs"
BQ_EVAL = f"{PROJECT_ID}.{BQ_DATASET}.evaluation_logs"

bq = bigquery.Client(project=f"{PROJECT_ID}")
#evaluator_model = genai.GenerativeModel("gemini-2.5-flash", generation_config={"temperature": 0.0})
vertexai.init(project=PROJECT_ID, location="us-west1") # or your preferred region
evaluator_model = GenerativeModel("gemini-2.5-flash") # Note: use standard names like gemini-1.5-flash

@functions_framework.http
def evaluator_agent(request):
    # --- ADD THIS BLOCK HERE ---
    if request.path == '/favicon.ico':
        return '', 204
    # ---------------------------
    request_json = request.get_json(silent=True)
    target_businesses = None
    if request_json and 'businesses' in request_json:
        target_businesses = request_json['businesses']

    if target_businesses:
        biz_list_str = ", ".join([f"'{b}'" for b in target_businesses])
        query_audit = f"SELECT business_name, credit_action, agent_summary FROM `{BQ_AUDIT}` WHERE business_name IN ({biz_list_str}) AND DATE(timestamp) = CURRENT_DATE()"
    else:
        query_audit = f"SELECT business_name, credit_action, agent_summary FROM `{BQ_AUDIT}` WHERE DATE(timestamp) = CURRENT_DATE()"

    actions_to_evaluate = list(bq.query(query_audit).result())

    if not actions_to_evaluate:
        return "Evaluator: No actions received.", 200

    results = []
    for action in actions_to_evaluate:
        biz = action.business_name
        query_reviews = f"SELECT review_text FROM `{BQ_REVIEWS}` WHERE business_name = @biz ORDER BY review_date DESC LIMIT 15"
        job_config = bigquery.QueryJobConfig(query_parameters=[bigquery.ScalarQueryParameter("biz", "STRING", biz)])
        recent_reviews = [r.review_text for r in bq.query(query_reviews, job_config=job_config).result()]
        reviews_str = "\n".join([f"- {r}" for r in recent_reviews])

        prompt = f"""
        You are a strict QA Auditor. 
        BUSINESS: {biz}
        AGENT 1 ACTION: {action.credit_action}
        AGENT 1 REASON: {action.agent_summary}
        Recent reviews:
        {reviews_str}

        Did Agent 1 make the right call to mark this business '{action.credit_action}'? 
        Format exactly:
        VERDICT: [AGREE or DISAGREE]
        REASON: [1 sentence why]
        """

        eval_response = evaluator_model.generate_content(prompt).text
        verdict = "DISAGREE" if "VERDICT: DISAGREE" in eval_response.upper() else "AGREE"
        reason = eval_response.replace(f"VERDICT: {verdict}", "").replace("REASON:", "").strip()

        bq.insert_rows_json(BQ_EVAL, [{
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "business_name": biz,
            "agent_1_action": action.credit_action,
            "evaluator_verdict": verdict,
            "evaluator_reason": reason
        }])
        results.append(f"{biz}: {verdict}")

    return f"Evaluated {len(actions_to_evaluate)} actions.\n" + "\n".join(results), 200