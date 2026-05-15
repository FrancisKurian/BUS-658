# BUS-658 Info Systems in Digital Times

## POC Multi-Agent Automated Review & Credit Evaluation Pipeline
This repository contains the source code to deploy a basic multi-agent system on Google Cloud Platform (GCP). The pipeline ingests business reviews, analyzes sentiment, triggers trend and compliance audits, and uses generative AI for a final evaluation cross-check.
## POC Overview

The system consists of three   agents triggered sequentially:
* Ingestion Agent (data_ingest_agent): Triggered daily by Cloud Scheduler. Reads raw data from Google Cloud Storage, runs sentiment classification using a BERT model, and saves scores to BigQuery. Implimented as cloud run functions with a python code with same name 'data_ingest_agent' attached to it.So to set this up in GCP, create a cloud run function an attached the code in this respository.
* Review Agent (daily_review_agent): Analyzes trends using NumPy, fetches historical data, logs decisions, and updates credit statuses.
* Evaluator Agent (evaluator_agent): Uses the Vertex AI SDK to send decision contexts to Gemini 1.5 Flash for a final automated cross-check.
* Visualization: All logs and decision contexts stream directly into Looker Studio dashboards.

