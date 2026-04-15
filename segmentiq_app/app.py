from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime
from functools import wraps
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from flask import Flask, jsonify, redirect, render_template, request, session, url_for
from flask_cors import CORS
from groq import Groq
from dotenv import load_dotenv
from sklearn.cluster import KMeans
from sklearn.decomposition import PCA
from sklearn.ensemble import RandomForestClassifier
from sklearn.impute import SimpleImputer
from sklearn.metrics import accuracy_score, f1_score, silhouette_score
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import StandardScaler
from werkzeug.security import check_password_hash, generate_password_hash

ROOT_DIR = Path(__file__).resolve().parent.parent
ARTIFACT_DIR = ROOT_DIR / "artifacts"
DATASET_PATH = ROOT_DIR / "Dataset_2.csv"
RANDOM_STATE = 42

load_dotenv(ROOT_DIR / ".env")

app = Flask(__name__, template_folder="templates", static_folder="static")
CORS(app)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "segmentiq-dev-secret-key")

MODEL_STATE: dict[str, Any] = {}
USER_DB_PATH = ROOT_DIR / "data" / "platform_users.db"

BUSINESS_SEGMENT_MAP = {
    0: "High Value Customers",
    1: "Potential Customers",
    2: "At-Risk Customers",
    3: "Low Engagement Customers",
}


def detect_id_column(df: pd.DataFrame) -> str | None:
    candidates = ["CUST_ID", "Customer_ID", "ID"]
    for col in candidates:
        if col in df.columns:
            return col
    return None


def utc_now_str() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


def get_user_db() -> sqlite3.Connection:
    USER_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(USER_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_user_portal_db() -> None:
    conn = get_user_db()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                full_name TEXT,
                created_at TEXT NOT NULL,
                last_login_at TEXT,
                login_count INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS user_profiles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER UNIQUE NOT NULL,
                segment_id INTEGER,
                segment_label TEXT,
                recommendation TEXT,
                recommended_actions_json TEXT,
                feature_json TEXT,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS campaign_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                segment_label TEXT,
                subject TEXT,
                offer_text TEXT,
                discount_pct REAL,
                recipient_count INTEGER,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def login_required(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        if not session.get("user_id"):
            return redirect(url_for("platform_login"))
        return func(*args, **kwargs)

    return wrapper


def predict_user_segment(feature_payload: dict[str, float]) -> dict[str, Any]:
    selected_features: list[str] = MODEL_STATE["selected_features"]
    imputer: SimpleImputer = MODEL_STATE["imputer"]
    scaler: StandardScaler = MODEL_STATE["scaler"]
    kmeans: KMeans = MODEL_STATE["kmeans"]
    rf_model: RandomForestClassifier = MODEL_STATE["rf_model"]
    outlier_bounds: dict[str, dict[str, float]] = MODEL_STATE.get("outlier_bounds", {})

    row: dict[str, float] = {}
    for idx, feature in enumerate(selected_features):
        value = feature_payload.get(feature, np.nan)
        try:
            row[feature] = float(value)
        except (TypeError, ValueError):
            row[feature] = float(imputer.statistics_[idx])

    input_df = pd.DataFrame([row], columns=selected_features)
    input_df = apply_outlier_bounds(input_df, outlier_bounds)
    input_imputed = imputer.transform(input_df)
    input_scaled = scaler.transform(input_imputed)

    rf_segment = int(rf_model.predict(input_df)[0])
    _ = int(kmeans.predict(input_scaled)[0])

    profile = MODEL_STATE.get("segment_profile", pd.DataFrame())
    names = build_segment_names(profile)
    segment_name = names.get(rf_segment, f"Segment {rf_segment}")
    rec = get_recommendation_actions(segment_name)
    return {
        "segmentId": rf_segment,
        "segmentLabel": segment_name,
        "recommendation": rec["headline"],
        "recommendedActions": rec["actions"],
    }


def get_user_profile_context(user_id: int) -> dict[str, Any]:
    conn = get_user_db()
    try:
        current_user = conn.execute(
            "SELECT id, email, full_name, login_count, last_login_at FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()

        saved_profile = conn.execute(
            """
            SELECT segment_id, segment_label, recommendation, recommended_actions_json, feature_json, updated_at
            FROM user_profiles
            WHERE user_id = ?
            """,
            (user_id,),
        ).fetchone()

        new_users = conn.execute(
            "SELECT id, email, full_name, login_count, created_at FROM users WHERE login_count <= 1 ORDER BY created_at DESC LIMIT 20"
        ).fetchall()
        returning_users = conn.execute(
            "SELECT id, email, full_name, login_count, last_login_at FROM users WHERE login_count > 1 ORDER BY last_login_at DESC LIMIT 20"
        ).fetchall()

        campaign_targets = conn.execute(
            """
            SELECT u.email, u.full_name, p.segment_label, p.recommendation, p.recommended_actions_json, p.updated_at
            FROM user_profiles p
            JOIN users u ON p.user_id = u.id
            ORDER BY p.updated_at DESC
            LIMIT 50
            """
        ).fetchall()
    finally:
        conn.close()

    targets = []
    for row in campaign_targets:
        actions = []
        try:
            actions = json.loads(row["recommended_actions_json"] or "[]")
        except json.JSONDecodeError:
            actions = []
        targets.append(
            {
                "email": row["email"],
                "full_name": row["full_name"],
                "segment_label": row["segment_label"],
                "recommendation": row["recommendation"],
                "actions": ", ".join(actions[:2]),
                "updated_at": row["updated_at"],
            }
        )

    profile_data = None
    if saved_profile:
        try:
            feature_data = json.loads(saved_profile["feature_json"] or "{}")
        except json.JSONDecodeError:
            feature_data = {}
        try:
            actions = json.loads(saved_profile["recommended_actions_json"] or "[]")
        except json.JSONDecodeError:
            actions = []

        profile_data = {
            "segment_id": saved_profile["segment_id"],
            "segment_label": saved_profile["segment_label"],
            "recommendation": saved_profile["recommendation"],
            "actions": actions,
            "feature_data": feature_data,
            "updated_at": saved_profile["updated_at"],
        }

    return {
        "current_user": dict(current_user) if current_user else None,
        "saved_profile": profile_data,
        "new_users": [dict(r) for r in new_users],
        "returning_users": [dict(r) for r in returning_users],
        "campaign_targets": targets,
    }


def save_user_profile(user_id: int, payload: dict[str, float]) -> dict[str, Any]:
    prediction = predict_user_segment(payload)

    conn = get_user_db()
    try:
        conn.execute(
            """
            INSERT INTO user_profiles (
                user_id, segment_id, segment_label, recommendation,
                recommended_actions_json, feature_json, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                segment_id = excluded.segment_id,
                segment_label = excluded.segment_label,
                recommendation = excluded.recommendation,
                recommended_actions_json = excluded.recommended_actions_json,
                feature_json = excluded.feature_json,
                updated_at = excluded.updated_at
            """,
            (
                user_id,
                int(prediction["segmentId"]),
                prediction["segmentLabel"],
                prediction["recommendation"],
                json.dumps(prediction["recommendedActions"]),
                json.dumps(payload),
                utc_now_str(),
            ),
        )
        conn.commit()
    finally:
        conn.close()

    return prediction


def build_admin_user_insights() -> dict[str, Any]:
    conn = get_user_db()
    try:
        total_users = int(conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"])
        new_users = int(conn.execute("SELECT COUNT(*) AS c FROM users WHERE login_count <= 1").fetchone()["c"])
        returning_users = int(conn.execute("SELECT COUNT(*) AS c FROM users WHERE login_count > 1").fetchone()["c"])

        usage_rows = conn.execute(
            """
            SELECT u.email, u.full_name, u.login_count, p.segment_label, p.feature_json, p.updated_at
            FROM users u
            LEFT JOIN user_profiles p ON p.user_id = u.id
            ORDER BY COALESCE(p.updated_at, u.created_at) DESC
            LIMIT 100
            """
        ).fetchall()

        logs = conn.execute(
            """
            SELECT segment_label, subject, offer_text, discount_pct, recipient_count, created_at
            FROM campaign_logs
            ORDER BY created_at DESC
            LIMIT 50
            """
        ).fetchall()
    finally:
        conn.close()

    usage = []
    segment_options: set[str] = set()
    for row in usage_rows:
        raw_features = row["feature_json"] or "{}"
        try:
            features = json.loads(raw_features)
        except json.JSONDecodeError:
            features = {}

        purchases = float(features.get("PURCHASES", 0.0) or 0.0)
        cash_adv = float(features.get("CASH_ADVANCE", 0.0) or 0.0)
        trx = float(features.get("PURCHASES_TRX", 0.0) or 0.0)
        segment_label = row["segment_label"] or "Unassigned"
        segment_options.add(segment_label)

        usage.append(
            {
                "email": row["email"],
                "full_name": row["full_name"] or "-",
                "segment_label": segment_label,
                "login_count": int(row["login_count"] or 0),
                "avg_purchases": round(purchases, 2),
                "purchases_trx": round(trx, 2),
                "cash_advance": round(cash_adv, 2),
                "updated_at": row["updated_at"] or "-",
            }
        )

    return {
        "summary": {
            "totalUsers": total_users,
            "newUsers": new_users,
            "returningUsers": returning_users,
        },
        "users": usage,
        "segmentOptions": sorted([s for s in segment_options if s and s != "Unassigned"]),
        "campaignLogs": [dict(r) for r in logs],
    }


def prepare_model_frame(df: pd.DataFrame) -> pd.DataFrame:
    id_col = detect_id_column(df)
    return df.drop(columns=[id_col], errors="ignore")


def compute_outlier_bounds(
    feature_df: pd.DataFrame,
    iqr_multiplier: float = 1.5,
    quantile_limits: tuple[float, float] = (0.01, 0.99),
) -> dict[str, dict[str, float]]:
    bounds: dict[str, dict[str, float]] = {}

    for col in feature_df.columns:
        series = pd.to_numeric(feature_df[col], errors="coerce").dropna()
        if len(series) < 8:
            continue

        q1 = float(series.quantile(0.25))
        q3 = float(series.quantile(0.75))
        iqr = q3 - q1
        if iqr <= 0:
            continue

        iqr_lower = q1 - iqr_multiplier * iqr
        iqr_upper = q3 + iqr_multiplier * iqr

        q_lower = float(series.quantile(quantile_limits[0]))
        q_upper = float(series.quantile(quantile_limits[1]))

        lower = max(iqr_lower, q_lower)
        upper = min(iqr_upper, q_upper)

        if lower >= upper:
            continue

        bounds[col] = {"lower": float(lower), "upper": float(upper)}

    return bounds


def apply_outlier_bounds(feature_df: pd.DataFrame, bounds: dict[str, dict[str, float]]) -> pd.DataFrame:
    if not bounds:
        return feature_df.copy()

    clipped = feature_df.copy()
    for col, limits in bounds.items():
        if col not in clipped.columns:
            continue
        clipped[col] = pd.to_numeric(clipped[col], errors="coerce").clip(
            lower=float(limits["lower"]),
            upper=float(limits["upper"]),
        )

    return clipped


def get_segment_label(segment_id: int) -> str:
    return BUSINESS_SEGMENT_MAP.get(int(segment_id), f"Segment {int(segment_id)}")


def get_recommendation_actions(segment_name: str) -> dict[str, Any]:
    recommendation_map: dict[str, dict[str, Any]] = {
        "High Value Customers": {
            "headline": "Protect and grow premium customer value.",
            "actions": [
                "Offer premium rewards",
                "Launch exclusive loyalty programs",
                "Increase credit limit for eligible accounts",
                "Run personalized cross-sell bundles",
            ],
        },
        "Potential Customers": {
            "headline": "Move mid-tier customers into high-value behavior.",
            "actions": [
                "Send targeted category offers",
                "Use installment-friendly campaigns",
                "Incentivize repeat purchases",
                "Promote card usage milestones",
            ],
        },
        "At-Risk Customers": {
            "headline": "Prevent churn and recover engagement quickly.",
            "actions": [
                "Send discounts and retention vouchers",
                "Launch re-engagement campaigns",
                "Offer debt-support and payment plans",
                "Trigger customer success follow-ups",
            ],
        },
        "Low Engagement Customers": {
            "headline": "Activate dormant users with low-friction nudges.",
            "actions": [
                "Run onboarding refresh campaigns",
                "Promote low-commitment starter offers",
                "Use app and email reminder journeys",
                "A/B test incentive thresholds",
            ],
        },
    }

    return recommendation_map.get(
        segment_name,
        {
            "headline": "Improve personalization with segment-focused offers.",
            "actions": [
                "Launch a focused campaign",
                "Review segment behavior weekly",
                "Test offer variants",
                "Optimize conversion path",
            ],
        },
    )


def activity_level_from_frequency(freq: float) -> str:
    if freq >= 0.7:
        return "High"
    if freq >= 0.35:
        return "Medium"
    return "Low"


def generate_auto_insights(segment_profile: pd.DataFrame, names: dict[int, str]) -> list[str]:
    if segment_profile.empty:
        return []

    profile = segment_profile.copy()
    insights: list[str] = []

    if "PURCHASES" in profile.columns and "customer_count" in profile.columns:
        weighted_purchase = profile["PURCHASES"] * profile["customer_count"]
        total_weighted_purchase = float(weighted_purchase.sum())
        if total_weighted_purchase > 0:
            top_seg = int(weighted_purchase.idxmax())
            top_share = float(weighted_purchase.loc[top_seg] / total_weighted_purchase) * 100
            insights.append(
                f"{names.get(top_seg, get_segment_label(top_seg))} contributes about {top_share:.1f}% of weighted purchase value. Prioritize retention and premium offers."
            )

    if "CASH_ADVANCE" in profile.columns:
        risk_seg = int(profile["CASH_ADVANCE"].idxmax())
        insights.append(
            f"Highest risk pressure appears in {names.get(risk_seg, get_segment_label(risk_seg))} based on cash-advance behavior. Use credit wellness and re-engagement programs."
        )

    if "PURCHASES_FREQUENCY" in profile.columns:
        low_eng_seg = int(profile["PURCHASES_FREQUENCY"].idxmin())
        insights.append(
            f"Lowest activity segment is {names.get(low_eng_seg, get_segment_label(low_eng_seg))}. Use activation campaigns with low-friction starter offers."
        )

    return insights


def build_retrain_insight(previous_profile: pd.DataFrame, current_profile: pd.DataFrame) -> dict[str, Any]:
    if current_profile.empty:
        return {"notes": ["No segment profile available after retraining."], "segmentDistribution": [], "patternChanges": []}

    names = build_segment_names(current_profile)
    distribution_rows: list[dict[str, Any]] = []
    pattern_changes: list[dict[str, Any]] = []

    for seg, row in current_profile.iterrows():
        seg_id = int(seg)
        distribution_rows.append(
            {
                "segment": seg_id,
                "segmentLabel": names.get(seg_id, get_segment_label(seg_id)),
                "customers": int(float(row.get("customer_count", 0))),
            }
        )

    if not previous_profile.empty and "PURCHASES" in current_profile.columns:
        for seg, row in current_profile.iterrows():
            seg_id = int(seg)
            if seg_id not in previous_profile.index:
                continue
            old_purchase = float(previous_profile.loc[seg_id].get("PURCHASES", 0.0))
            new_purchase = float(row.get("PURCHASES", 0.0))
            delta_purchase = new_purchase - old_purchase
            old_cash = float(previous_profile.loc[seg_id].get("CASH_ADVANCE", 0.0))
            new_cash = float(row.get("CASH_ADVANCE", 0.0))
            delta_cash = new_cash - old_cash
            pattern_changes.append(
                {
                    "segment": seg_id,
                    "segmentLabel": names.get(seg_id, get_segment_label(seg_id)),
                    "purchaseDelta": round(delta_purchase, 3),
                    "cashAdvanceDelta": round(delta_cash, 3),
                }
            )

    notes = [
        "Retrain completed. Review segment shifts before launching campaigns.",
        "Large negative purchase delta may indicate demand cooling in a segment.",
    ]
    return {
        "notes": notes,
        "segmentDistribution": distribution_rows,
        "patternChanges": pattern_changes,
    }


def build_business_decision_dashboard(state: dict[str, Any]) -> dict[str, Any]:
    profile = state.get("segment_profile", pd.DataFrame())
    if profile.empty:
        return {
            "mostValuableSegment": None,
            "mostRiskySegment": None,
            "segmentDistribution": [],
            "segmentSummary": [],
            "insightsPanel": [],
            "suggestedActions": [],
            "finalDecision": {
                "focus": "-",
                "risk": "-",
                "strategy": "No strategy available because segment data is missing.",
            },
            "autoSummary": "No segment data available yet.",
        }

    names = build_segment_names(profile)

    if "PURCHASES" in profile.columns:
        valuable_seg = int(profile["PURCHASES"].idxmax())
    else:
        valuable_seg = int(profile.index[0])

    if "CASH_ADVANCE" in profile.columns:
        risky_seg = int(profile["CASH_ADVANCE"].idxmax())
    else:
        risky_seg = int(profile.index[0])

    total_customers = float(profile.get("customer_count", pd.Series(dtype=float)).sum()) or 1.0
    distribution = []
    segment_summary = []
    for seg, row in profile.iterrows():
        seg_id = int(seg)
        count_value = int(float(row.get("customer_count", 0)))
        share_pct = round((count_value / total_customers) * 100, 2)
        distribution.append(
            {
                "segment": seg_id,
                "segmentLabel": names.get(seg_id, get_segment_label(seg_id)),
                "count": count_value,
            }
        )
        segment_summary.append(
            {
                "segment": seg_id,
                "segmentLabel": names.get(seg_id, get_segment_label(seg_id)),
                "sharePct": share_pct,
            }
        )

    risky_actions = get_recommendation_actions(names.get(risky_seg, get_segment_label(risky_seg)))
    valuable_actions = get_recommendation_actions(names.get(valuable_seg, get_segment_label(valuable_seg)))

    auto_summary = (
        f"Most valuable segment is {names.get(valuable_seg, get_segment_label(valuable_seg))}. "
        f"Most risky segment is {names.get(risky_seg, get_segment_label(risky_seg))}."
    )

    insights_panel = [
        f"{names.get(valuable_seg, get_segment_label(valuable_seg))} customers generate the strongest revenue signal.",
        f"{names.get(risky_seg, get_segment_label(risky_seg))} has elevated risk behavior and needs retention action.",
    ]

    return {
        "mostValuableSegment": {
            "segment": valuable_seg,
            "segmentLabel": names.get(valuable_seg, get_segment_label(valuable_seg)),
        },
        "mostRiskySegment": {
            "segment": risky_seg,
            "segmentLabel": names.get(risky_seg, get_segment_label(risky_seg)),
        },
        "segmentDistribution": distribution,
        "segmentSummary": segment_summary,
        "insightsPanel": insights_panel,
        "suggestedActions": [
            {
                "focus": "Value Growth",
                "segment": names.get(valuable_seg, get_segment_label(valuable_seg)),
                "actions": valuable_actions["actions"],
            },
            {
                "focus": "Risk Mitigation",
                "segment": names.get(risky_seg, get_segment_label(risky_seg)),
                "actions": risky_actions["actions"],
            },
        ],
        "finalDecision": {
            "focus": names.get(valuable_seg, get_segment_label(valuable_seg)),
            "risk": names.get(risky_seg, get_segment_label(risky_seg)),
            "strategy": "Retention + Upselling",
        },
        "autoSummary": auto_summary,
    }


def dataset_quality_report(df: pd.DataFrame, reference_features: list[str] | None = None) -> dict[str, Any]:
    model_df = prepare_model_frame(df)
    numeric_df = model_df.select_dtypes(include=[np.number])

    total_columns = int(model_df.shape[1])
    numeric_columns = numeric_df.columns.tolist()
    numeric_ratio = float(len(numeric_columns) / total_columns) if total_columns else 0.0

    missing_by_column = model_df.isna().mean().sort_values(ascending=False)
    missing_ratio = float(model_df.isna().sum().sum() / (model_df.shape[0] * max(total_columns, 1)))
    top_missing = [
        {"feature": col, "missingRatio": float(round(pct, 4))}
        for col, pct in missing_by_column.head(5).items()
        if pct > 0
    ]

    warnings: list[str] = []
    critical: list[str] = []

    if total_columns == 0:
        critical.append("Dataset does not contain usable columns.")
    if len(numeric_columns) == 0:
        critical.append("Dataset does not contain numeric columns for ML training.")
    if numeric_ratio < 0.6:
        warnings.append("Numeric coverage is low for K-Means and PCA. Consider adding more numeric fields.")
    if missing_ratio > 0.1:
        warnings.append("Missing values are high. Stronger imputation or cleaning is recommended before retraining.")

    outlier_flags: list[dict[str, Any]] = []
    for col in numeric_columns:
        series = numeric_df[col].dropna()
        if len(series) < 8:
            continue
        q1 = float(series.quantile(0.25))
        q3 = float(series.quantile(0.75))
        iqr = q3 - q1
        if iqr <= 0:
            continue
        lower = q1 - 1.5 * iqr
        upper = q3 + 1.5 * iqr
        outlier_ratio = float(((series < lower) | (series > upper)).mean())
        if outlier_ratio >= 0.05:
            outlier_flags.append({"feature": col, "outlierRatio": round(outlier_ratio, 4)})

    if outlier_flags:
        warnings.append("Some numeric columns have notable outliers. Review scaling or clipping before retraining.")

    schema_gap = {"missingColumns": [], "extraColumns": [], "overlapRatio": None}
    if reference_features:
        missing_columns = [feature for feature in reference_features if feature not in model_df.columns]
        extra_columns = [col for col in model_df.columns if col not in reference_features]
        overlap = len([feature for feature in reference_features if feature in model_df.columns]) / max(len(reference_features), 1)
        schema_gap = {
            "missingColumns": missing_columns,
            "extraColumns": extra_columns,
            "overlapRatio": round(float(overlap), 4),
        }
        if missing_columns:
            warnings.append("Uploaded dataset does not fully match the currently deployed feature schema.")

    ready_for_retrain = not critical and numeric_ratio >= 0.6 and missing_ratio <= 0.1
    if reference_features and schema_gap["overlapRatio"] is not None and schema_gap["overlapRatio"] < 0.5:
        ready_for_retrain = False
        critical.append("Feature overlap with the current model is too low for a safe retrain.")

    return {
        "rows": int(df.shape[0]),
        "columns": int(df.shape[1]),
        "modelColumns": total_columns,
        "numericColumns": len(numeric_columns),
        "numericRatio": round(numeric_ratio, 4),
        "missingRatio": round(missing_ratio, 4),
        "topMissing": top_missing,
        "outlierFlags": outlier_flags,
        "schemaGap": schema_gap,
        "warnings": warnings,
        "critical": critical,
        "readyForRetrain": ready_for_retrain,
    }


def build_prediction_explanation(
    input_imputed_df: pd.DataFrame,
    selected_features: list[str],
    imputer: SimpleImputer,
    scaler: StandardScaler,
    rf_model: RandomForestClassifier,
) -> dict[str, Any]:
    imputed_array = imputer.transform(input_imputed_df)
    scaled_array = scaler.transform(imputed_array)
    importances = rf_model.feature_importances_

    rows: list[dict[str, Any]] = []
    for idx, feature in enumerate(selected_features):
        current_value = float(imputed_array[0][idx])
        baseline = float(imputer.statistics_[idx])
        scaled_value = float(abs(scaled_array[0][idx]))
        importance = float(importances[idx])
        influence = float(scaled_value * importance)
        if current_value > baseline:
            direction = "above typical"
        elif current_value < baseline:
            direction = "below typical"
        else:
            direction = "at baseline"
        rows.append(
            {
                "feature": feature,
                "value": round(current_value, 4),
                "baseline": round(baseline, 4),
                "importance": round(importance, 4),
                "influence": round(influence, 4),
                "direction": direction,
            }
        )

    rows.sort(key=lambda item: item["influence"], reverse=True)
    top_factors = rows[:3]
    if top_factors:
        summary_bits = [f"{item['feature']} ({item['direction']})" for item in top_factors]
        summary = "Top drivers: " + ", ".join(summary_bits) + "."
    else:
        summary = "Top drivers could not be computed for this prediction."

    return {
        "summary": summary,
        "topFactors": top_factors,
        "confidenceProxy": round(float(sum(item["importance"] for item in top_factors)), 4),
    }


def build_business_kpi_layer(state: dict[str, Any]) -> dict[str, Any]:
    segment_profile = state.get("segment_profile", pd.DataFrame())
    metrics = state.get("metrics", {})

    if segment_profile.empty:
        return {"segments": [], "summaryCards": [], "notes": ["No segment profile is available yet."]}

    profile = segment_profile.copy()
    if "customer_count" not in profile.columns:
        profile["customer_count"] = 0

    total_customers = float(profile["customer_count"].sum()) or 1.0
    overall_purchases = float((profile.get("PURCHASES", pd.Series(dtype=float)) * profile["customer_count"]).sum() / total_customers)
    overall_purchase_freq = float((profile.get("PURCHASES_FREQUENCY", pd.Series(dtype=float)) * profile["customer_count"]).sum() / total_customers)
    overall_cash_advance = float((profile.get("CASH_ADVANCE", pd.Series(dtype=float)) * profile["customer_count"]).sum() / total_customers)

    segment_rows: list[dict[str, Any]] = []
    names = build_segment_names(profile)
    for seg, row in profile.iterrows():
        customer_count = float(row.get("customer_count", 0))
        customer_share = customer_count / total_customers if total_customers else 0.0
        purchases = float(row.get("PURCHASES", overall_purchases))
        purchase_freq = float(row.get("PURCHASES_FREQUENCY", overall_purchase_freq))
        cash_advance = float(row.get("CASH_ADVANCE", overall_cash_advance))
        full_payment = float(row.get("PRC_FULL_PAYMENT", 0.0))

        revenue_uplift = 0.0
        if overall_purchases > 0:
            revenue_uplift = ((purchases - overall_purchases) / overall_purchases) * 100

        churn_risk_reduction = 100 - min(max((cash_advance / (overall_cash_advance + 1e-6)) * 20, 0), 35)
        conversion_estimate = (purchase_freq * 60) + (full_payment * 30) + min(max(customer_share * 50, 0), 10)
        conversion_estimate = max(5.0, min(95.0, conversion_estimate))

        seg_name = names.get(int(seg), get_segment_label(int(seg)))
        rec = get_recommendation_actions(seg_name)
        segment_rows.append(
            {
                "segment": int(seg),
                "segmentLabel": seg_name,
                "customerCount": int(customer_count),
                "customerShare": round(customer_share, 4),
                "expectedRevenueUpliftPct": round(float(np.clip(revenue_uplift, -25, 60)), 2),
                "churnRiskReductionPct": round(float(np.clip(churn_risk_reduction, 5, 95)), 2),
                "campaignConversionEstimatePct": round(float(conversion_estimate), 2),
                "averageSpending": round(purchases, 2),
                "averageIncome": round(float(row.get("CREDIT_LIMIT", row.get("PAYMENTS", 0.0))), 2),
                "activityLevel": activity_level_from_frequency(purchase_freq),
                "recommendationHeadline": rec["headline"],
                "recommendedActions": rec["actions"],
            }
        )

    active_users = sum(
        int(float(row.get("customer_count", 0)))
        for _, row in profile.iterrows()
        if float(row.get("PURCHASES_FREQUENCY", overall_purchase_freq)) >= 0.35
    )

    top_segment = max(segment_rows, key=lambda item: item.get("averageSpending", 0.0), default=None)
    risk_segment = max(segment_rows, key=lambda item: item.get("churnRiskReductionPct", 0.0) * -1, default=None)

    summary_cards = [
        {
            "label": "Total Customers",
            "value": int(total_customers),
        },
        {
            "label": "Avg Spending",
            "value": f"{overall_purchases:.2f}",
        },
        {
            "label": "Active Users",
            "value": int(active_users),
        },
        {
            "label": "Final Silhouette",
            "value": f"{float(metrics.get('final_silhouette', 0.0)):.4f}",
        },
    ]

    return {
        "segments": segment_rows,
        "summaryCards": summary_cards,
        "topSegment": top_segment,
        "riskSegment": risk_segment,
        "notes": [
            "KPI values are directional estimates derived from segment behavior patterns.",
            "Use them to prioritize campaigns, not as financial forecasts.",
        ],
    }


def load_artifacts() -> dict[str, Any]:
    if not ARTIFACT_DIR.exists():
        raise FileNotFoundError(f"Artifact directory not found: {ARTIFACT_DIR}")

    with open(ARTIFACT_DIR / "selected_features.json", "r", encoding="utf-8") as f:
        selected_features = json.load(f)

    state: dict[str, Any] = {
        "imputer": joblib.load(ARTIFACT_DIR / "imputer.joblib"),
        "scaler": joblib.load(ARTIFACT_DIR / "scaler.joblib"),
        "kmeans": joblib.load(ARTIFACT_DIR / "kmeans.joblib"),
        "rf_model": joblib.load(ARTIFACT_DIR / "rf_model.joblib"),
        "selected_features": selected_features,
    }

    metrics_path = ARTIFACT_DIR / "metrics.json"
    if metrics_path.exists():
        with open(metrics_path, "r", encoding="utf-8") as f:
            state["metrics"] = json.load(f)
    else:
        state["metrics"] = {}

    profile_path = ARTIFACT_DIR / "segment_profile.csv"
    if profile_path.exists():
        state["segment_profile"] = pd.read_csv(profile_path, index_col=0)
    else:
        state["segment_profile"] = pd.DataFrame()

    bounds_path = ARTIFACT_DIR / "outlier_bounds.json"
    if bounds_path.exists():
        with open(bounds_path, "r", encoding="utf-8") as f:
            state["outlier_bounds"] = json.load(f)
    else:
        state["outlier_bounds"] = {}

    return state


def save_artifacts(
    imputer: SimpleImputer,
    scaler: StandardScaler,
    kmeans: KMeans,
    rf_model: RandomForestClassifier,
    selected_features: list[str],
    metrics_payload: dict[str, Any],
    segment_profile: pd.DataFrame,
    outlier_bounds: dict[str, dict[str, float]],
) -> None:
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)

    joblib.dump(imputer, ARTIFACT_DIR / "imputer.joblib")
    joblib.dump(scaler, ARTIFACT_DIR / "scaler.joblib")
    joblib.dump(kmeans, ARTIFACT_DIR / "kmeans.joblib")
    joblib.dump(rf_model, ARTIFACT_DIR / "rf_model.joblib")

    with open(ARTIFACT_DIR / "selected_features.json", "w", encoding="utf-8") as f:
        json.dump(selected_features, f, indent=2)

    with open(ARTIFACT_DIR / "metrics.json", "w", encoding="utf-8") as f:
        json.dump(metrics_payload, f, indent=2)

    with open(ARTIFACT_DIR / "outlier_bounds.json", "w", encoding="utf-8") as f:
        json.dump(outlier_bounds, f, indent=2)

    segment_profile.to_csv(ARTIFACT_DIR / "segment_profile.csv")


def build_segment_names(segment_profile: pd.DataFrame) -> dict[int, str]:
    if segment_profile.empty:
        return {}

    names: dict[int, str] = {}
    for seg in segment_profile.index:
        seg_int = int(seg)
        names[seg_int] = get_segment_label(seg_int)

    return names


def rule_based_nova(question: str, state: dict[str, Any]) -> str:
    question_l = question.lower().strip()
    profile = state.get("segment_profile", pd.DataFrame())
    names = build_segment_names(profile)

    if profile.empty:
        return "NOVA does not have segment profile data yet. Retrain the model from Admin and try again."

    if "spend" in question_l or "purchase" in question_l:
        if "PURCHASES" in profile.columns:
            top_seg = int(profile["PURCHASES"].idxmax())
            top_value = float(profile.loc[top_seg, "PURCHASES"])
            seg_name = names.get(top_seg, f"Segment {top_seg}")
            return (
                f"{seg_name} (Segment {top_seg}) spends the most on average, with PURCHASES around "
                f"{top_value:,.2f}. Prioritize premium offers and loyalty benefits for this segment."
            )

    if "target segment" in question_l or "segment" in question_l:
        numbers = [int(ch) for ch in question_l if ch.isdigit()]
        if numbers:
            seg = numbers[0]
            if seg in profile.index:
                row = profile.loc[seg]
                purchases = row.get("PURCHASES", np.nan)
                cash_adv = row.get("CASH_ADVANCE", np.nan)
                action = "Run lifecycle campaigns and improve engagement frequency."
                if np.isfinite(purchases) and purchases > profile["PURCHASES"].median():
                    action = "Offer premium bundles, installment offers, and card-upgrade campaigns."
                if np.isfinite(cash_adv) and cash_adv > profile["CASH_ADVANCE"].median():
                    action = "Push debt-management plans and responsible credit reminders."
                seg_name = names.get(seg, f"Segment {seg}")
                rec = get_recommendation_actions(seg_name)
                return (
                    f"For {seg_name} (Segment {seg}), recommended strategy: {action} "
                    f"Top actions: {', '.join(rec['actions'][:2])}. "
                    "Use personalized communication and monitor conversion in weekly cohorts."
                )

        if "PURCHASES" in profile.columns:
            best_seg = int(profile["PURCHASES"].idxmax())
            seg_name = names.get(best_seg, f"Segment {best_seg}")
            rec = get_recommendation_actions(seg_name)
            return (
                f"Target {seg_name} (Segment {best_seg}) first for highest business impact. "
                f"Recommendation: {rec['headline']} Top actions: {', '.join(rec['actions'][:2])}."
            )

    return (
        "NOVA recommendation: ask specific business questions like 'Which segment spends the most?' or "
        "'How should I target segment 2?'."
    )


def groq_response(question: str, state: dict[str, Any]) -> str | None:
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        return None

    profile = state.get("segment_profile", pd.DataFrame())
    metrics = state.get("metrics", {})

    context = {
        "metrics": {
            "selected_k": metrics.get("selected_k"),
            "best_k_by_silhouette": metrics.get("best_k_by_silhouette"),
            "final_silhouette": metrics.get("final_silhouette"),
            "rf_accuracy": metrics.get("rf_accuracy"),
        },
        "segment_profile": profile.reset_index().to_dict(orient="records") if not profile.empty else [],
        "segment_labels": build_segment_names(profile),
        "decision_dashboard": build_business_decision_dashboard(state),
    }

    client = Groq(api_key=api_key)
    try:
        completion = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            temperature=0.2,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a business analyst AI for SegmentIQ. Provide concise, non-technical recommendations "
                        "that a business owner can execute. Prioritize growth, retention, and campaign actions. "
                        "Ground answers strictly in provided context."
                    ),
                },
                {"role": "user", "content": f"Context: {json.dumps(context)}\n\nQuestion: {question}"},
            ],
        )
        return completion.choices[0].message.content
    except Exception:
        return None


def get_nova_status() -> dict[str, Any]:
    api_key = os.getenv("GROQ_API_KEY", "").strip()
    if api_key:
        return {
            "mode": "groq",
            "active": True,
            "label": "Groq active",
            "description": "NOVA is using Groq-powered responses.",
        }

    return {
        "mode": "fallback",
        "active": False,
        "label": "Fallback mode",
        "description": "NOVA is using rule-based insights.",
    }


def train_pipeline(dataset_path: Path, business_k: int = 4) -> dict[str, Any]:
    df = pd.read_csv(dataset_path)

    id_col = detect_id_column(df)
    feature_df = df.drop(columns=[id_col], errors="ignore").select_dtypes(include=[np.number]).copy()
    selected_features = feature_df.columns.tolist()

    outlier_bounds = compute_outlier_bounds(feature_df)
    feature_df_clipped = apply_outlier_bounds(feature_df, outlier_bounds)

    imputer = SimpleImputer(strategy="median")
    scaler = StandardScaler()

    X_imputed = imputer.fit_transform(feature_df_clipped)
    X_scaled = scaler.fit_transform(X_imputed)

    metrics_rows: list[dict[str, float]] = []
    for k in range(2, 11):
        km = KMeans(n_clusters=k, random_state=RANDOM_STATE, n_init=10)
        labels = km.fit_predict(X_scaled)
        metrics_rows.append(
            {
                "k": int(k),
                "inertia": float(km.inertia_),
                "silhouette": float(silhouette_score(X_scaled, labels)),
            }
        )

    k_metrics = pd.DataFrame(metrics_rows)
    best_k = int(k_metrics.loc[k_metrics["silhouette"].idxmax(), "k"])

    selected_k = int(business_k)
    kmeans = KMeans(n_clusters=selected_k, random_state=RANDOM_STATE, n_init=10)
    cluster_labels = kmeans.fit_predict(X_scaled)

    final_silhouette = float(silhouette_score(X_scaled, cluster_labels))

    X_imputed_df = pd.DataFrame(X_imputed, columns=selected_features)
    X_train, X_test, y_train, y_test = train_test_split(
        X_imputed_df,
        cluster_labels,
        test_size=0.2,
        random_state=RANDOM_STATE,
        stratify=cluster_labels,
    )

    rf_model = RandomForestClassifier(
        n_estimators=300,
        random_state=RANDOM_STATE,
        class_weight="balanced",
    )
    rf_model.fit(X_train, y_train)
    y_pred = rf_model.predict(X_test)

    rf_accuracy = float(accuracy_score(y_test, y_pred))
    rf_f1_macro = float(f1_score(y_test, y_pred, average="macro"))

    model_df = df.copy()
    model_df[selected_features] = feature_df_clipped[selected_features]
    model_df["Segment"] = cluster_labels

    segment_profile = model_df.groupby("Segment")[selected_features].mean().round(4)
    segment_profile["customer_count"] = model_df["Segment"].value_counts().sort_index()

    metrics_payload = {
        "selected_k": selected_k,
        "best_k_by_silhouette": best_k,
        "final_silhouette": final_silhouette,
        "rf_accuracy": rf_accuracy,
        "rf_f1_macro": rf_f1_macro,
        "outlier_treatment": {
            "enabled": True,
            "method": "IQR clipped to [1st, 99th] percentile envelope",
            "features_with_bounds": int(len(outlier_bounds)),
        },
        "k_metrics": k_metrics.to_dict(orient="records"),
    }

    save_artifacts(
        imputer=imputer,
        scaler=scaler,
        kmeans=kmeans,
        rf_model=rf_model,
        selected_features=selected_features,
        metrics_payload=metrics_payload,
        segment_profile=segment_profile,
        outlier_bounds=outlier_bounds,
    )

    return {
        "rows": int(df.shape[0]),
        "features": int(len(selected_features)),
        "selected_k": selected_k,
        "best_k_by_silhouette": best_k,
        "final_silhouette": final_silhouette,
        "rf_accuracy": rf_accuracy,
        "rf_f1_macro": rf_f1_macro,
    }


def make_dashboard_payload(state: dict[str, Any], sample_size: int = 2500) -> dict[str, Any]:
    df = pd.read_csv(DATASET_PATH)
    id_col = detect_id_column(df)

    selected_features: list[str] = state["selected_features"]
    feature_df = df[selected_features].copy()
    outlier_bounds: dict[str, dict[str, float]] = state.get("outlier_bounds", {})
    feature_df = apply_outlier_bounds(feature_df, outlier_bounds)

    imputer: SimpleImputer = state["imputer"]
    scaler: StandardScaler = state["scaler"]
    kmeans: KMeans = state["kmeans"]
    rf_model: RandomForestClassifier = state["rf_model"]

    X_imputed = imputer.transform(feature_df)
    X_scaled = scaler.transform(X_imputed)

    labels = kmeans.predict(X_scaled)

    pca = PCA(n_components=2, random_state=RANDOM_STATE)
    coords = pca.fit_transform(X_scaled)

    plot_df = pd.DataFrame({"PC1": coords[:, 0], "PC2": coords[:, 1], "Segment": labels})
    if len(plot_df) > sample_size:
        plot_df = plot_df.sample(sample_size, random_state=RANDOM_STATE)

    segment_counts = pd.Series(labels).value_counts().sort_index()

    corr = pd.DataFrame(X_imputed, columns=selected_features).corr().round(4)

    importances = pd.Series(rf_model.feature_importances_, index=selected_features).sort_values(ascending=False)

    segment_profile = state.get("segment_profile", pd.DataFrame())
    names = build_segment_names(segment_profile)

    insights: list[str] = []
    if not segment_profile.empty and "PURCHASES" in segment_profile.columns:
        top_seg = int(segment_profile["PURCHASES"].idxmax())
        insights.append(f"Top spending segment: {names.get(top_seg, get_segment_label(top_seg))} (Segment {top_seg}).")
    if not segment_profile.empty and "CASH_ADVANCE" in segment_profile.columns:
        cash_seg = int(segment_profile["CASH_ADVANCE"].idxmax())
        insights.append(f"Highest cash-advance behavior: {names.get(cash_seg, get_segment_label(cash_seg))}. Add credit wellness campaigns.")
    insights.extend(generate_auto_insights(segment_profile, names))
    insights.append("Use Admin > Retrain after uploading a new dataset to refresh segments and insights.")

    segment_summary_cards: list[dict[str, Any]] = []
    if not segment_profile.empty:
        for seg, row in segment_profile.iterrows():
            seg_id = int(seg)
            freq = float(row.get("PURCHASES_FREQUENCY", 0.0))
            segment_summary_cards.append(
                {
                    "segment": seg_id,
                    "segmentLabel": names.get(seg_id, get_segment_label(seg_id)),
                    "customerCount": int(float(row.get("customer_count", 0))),
                    "averageSpending": round(float(row.get("PURCHASES", 0.0)), 2),
                    "averageIncome": round(float(row.get("CREDIT_LIMIT", row.get("PAYMENTS", 0.0))), 2),
                    "activityLevel": activity_level_from_frequency(freq),
                }
            )

    return {
        "metrics": state.get("metrics", {}),
        "segmentNames": names,
        "insights": insights,
        "segmentDistribution": {
            "segments": [int(x) for x in segment_counts.index.tolist()],
            "labels": [names.get(int(x), get_segment_label(int(x))) for x in segment_counts.index.tolist()],
            "counts": [int(x) for x in segment_counts.values.tolist()],
        },
        "pcaScatter": {
            "points": plot_df.to_dict(orient="records"),
            "explainedVariance2D": float(pca.explained_variance_ratio_.sum()),
        },
        "correlation": {
            "columns": corr.columns.tolist(),
            "matrix": corr.values.tolist(),
        },
        "featureImportance": {
            "features": importances.index.tolist()[:10],
            "scores": [float(v) for v in importances.values.tolist()[:10]],
        },
        "segmentSummaryCards": segment_summary_cards,
        "decisionDashboard": build_business_decision_dashboard(state),
        "segmentProfile": segment_profile.reset_index().to_dict(orient="records") if not segment_profile.empty else [],
    }


def dataset_algorithm_fit() -> dict[str, Any]:
    df = pd.read_csv(DATASET_PATH)
    id_col = detect_id_column(df)
    model_df = df.drop(columns=[id_col], errors="ignore")

    num_cols = model_df.select_dtypes(include=[np.number]).columns.tolist()
    total_cols = int(model_df.shape[1])
    numeric_ratio = float(len(num_cols) / total_cols) if total_cols else 0.0

    missing_ratio = float(model_df.isna().sum().sum() / (model_df.shape[0] * max(model_df.shape[1], 1)))

    recommendation = [
        "K-Means: Strong fit (numeric-heavy credit behavior features).",
        "Random Forest: Strong fit for segment prediction and feature importance.",
        "PCA: Recommended for visual analytics and cluster projection.",
    ]

    if numeric_ratio < 0.6:
        recommendation[0] = "K-Means: Moderate fit. Increase numeric features or encode categorical features."
    if missing_ratio > 0.1:
        recommendation.append("Data quality warning: missing ratio is high. Improve imputation strategy before retraining.")

    return {
        "rows": int(df.shape[0]),
        "columns": int(df.shape[1]),
        "numericColumns": len(num_cols),
        "numericRatio": round(numeric_ratio, 4),
        "missingRatio": round(missing_ratio, 4),
        "recommendedAlgorithms": recommendation,
    }


@app.route("/")
def index() -> str:
    return render_template("index.html")


@app.route("/platform")
@app.route("/platform/")
def platform_home() -> Any:
    if session.get("user_id"):
        return redirect(url_for("platform_profile"))
    return redirect(url_for("platform_login"))


@app.route("/platform/login", methods=["GET", "POST"])
@app.route("/platform/login/", methods=["GET", "POST"])
def platform_login() -> Any:
    message = ""
    if request.method == "POST":
        email = str(request.form.get("email", "")).strip().lower()
        password = str(request.form.get("password", ""))

        conn = get_user_db()
        try:
            user = conn.execute(
                "SELECT id, email, password_hash FROM users WHERE email = ?",
                (email,),
            ).fetchone()

            if not user or not check_password_hash(user["password_hash"], password):
                message = "Invalid email or password."
            else:
                session["user_id"] = int(user["id"])
                session["user_email"] = str(user["email"])
                conn.execute(
                    """
                    UPDATE users
                    SET login_count = login_count + 1,
                        last_login_at = ?
                    WHERE id = ?
                    """,
                    (utc_now_str(), int(user["id"])),
                )
                conn.commit()
                return redirect(url_for("platform_profile"))
        finally:
            conn.close()

    return render_template("platform_login.html", message=message)


@app.route("/platform/register", methods=["GET", "POST"])
@app.route("/platform/register/", methods=["GET", "POST"])
def platform_register() -> Any:
    message = ""
    if request.method == "POST":
        full_name = str(request.form.get("full_name", "")).strip()
        email = str(request.form.get("email", "")).strip().lower()
        password = str(request.form.get("password", ""))

        if not email or not password:
            message = "Email and password are required."
        else:
            conn = get_user_db()
            try:
                existing = conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
                if existing:
                    message = "Email already exists. Use login."
                else:
                    conn.execute(
                        """
                        INSERT INTO users (email, password_hash, full_name, created_at, last_login_at, login_count)
                        VALUES (?, ?, ?, ?, ?, 0)
                        """,
                        (
                            email,
                            generate_password_hash(password),
                            full_name,
                            utc_now_str(),
                            None,
                        ),
                    )
                    conn.commit()
                    return redirect(url_for("platform_login"))
            finally:
                conn.close()

    return render_template("platform_register.html", message=message)


@app.route("/platform/logout")
@app.route("/platform/logout/")
def platform_logout() -> Any:
    session.clear()
    return redirect(url_for("platform_login"))


@app.route("/platform/profile", methods=["GET", "POST"])
@app.route("/platform/profile/", methods=["GET", "POST"])
@login_required
def platform_profile() -> Any:
    user_id = int(session["user_id"])
    selected_features: list[str] = MODEL_STATE["selected_features"]
    imputer: SimpleImputer = MODEL_STATE["imputer"]
    defaults = {feature: float(value) for feature, value in zip(selected_features, imputer.statistics_)}

    message = ""
    if request.method == "POST":
        payload: dict[str, float] = {}
        for feature in selected_features:
            raw = request.form.get(feature, "")
            try:
                payload[feature] = float(raw)
            except (TypeError, ValueError):
                payload[feature] = defaults[feature]
        prediction = save_user_profile(user_id, payload)
        message = (
            f"Profile saved. Assigned segment: {prediction['segmentLabel']}. "
            f"Recommended actions: {', '.join(prediction['recommendedActions'][:2])}."
        )

    context = get_user_profile_context(user_id)
    return render_template(
        "platform_profile.html",
        message=message,
        features=selected_features,
        defaults=defaults,
        context=context,
    )


@app.route("/api/user/status")
def api_user_status() -> Any:
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"loggedIn": False})

    context = get_user_profile_context(int(user_id))
    return jsonify({
        "loggedIn": True,
        "user": context.get("current_user"),
    })


@app.route("/api/user/register", methods=["POST"])
def api_user_register() -> Any:
    payload = request.get_json(silent=True) or {}
    full_name = str(payload.get("full_name", "")).strip()
    email = str(payload.get("email", "")).strip().lower()
    password = str(payload.get("password", ""))

    if not email or not password:
        return jsonify({"error": "Email and password are required."}), 400

    conn = get_user_db()
    try:
        existing = conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
        if existing:
            return jsonify({"error": "Email already exists."}), 400

        conn.execute(
            """
            INSERT INTO users (email, password_hash, full_name, created_at, last_login_at, login_count)
            VALUES (?, ?, ?, ?, ?, 1)
            """,
            (
                email,
                generate_password_hash(password),
                full_name,
                utc_now_str(),
                utc_now_str(),
            ),
        )
        new_user_id = conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()["id"]
        conn.commit()
        session["user_id"] = int(new_user_id)
        session["user_email"] = email
        return jsonify({"message": "Registration successful and logged in."})
    finally:
        conn.close()


@app.route("/api/user/login", methods=["POST"])
def api_user_login() -> Any:
    payload = request.get_json(silent=True) or {}
    email = str(payload.get("email", "")).strip().lower()
    password = str(payload.get("password", ""))

    conn = get_user_db()
    try:
        user = conn.execute(
            "SELECT id, email, password_hash FROM users WHERE email = ?",
            (email,),
        ).fetchone()

        if not user or not check_password_hash(user["password_hash"], password):
            return jsonify({"error": "Invalid email or password."}), 401

        session["user_id"] = int(user["id"])
        session["user_email"] = str(user["email"])
        conn.execute(
            """
            UPDATE users
            SET login_count = login_count + 1,
                last_login_at = ?
            WHERE id = ?
            """,
            (utc_now_str(), int(user["id"])),
        )
        conn.commit()
        return jsonify({"message": "Login successful."})
    finally:
        conn.close()


@app.route("/api/user/logout", methods=["POST"])
def api_user_logout() -> Any:
    session.clear()
    return jsonify({"message": "Logged out."})


@app.route("/api/user/profile", methods=["GET", "POST"])
def api_user_profile() -> Any:
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"error": "Unauthorized"}), 401

    selected_features: list[str] = MODEL_STATE["selected_features"]
    imputer: SimpleImputer = MODEL_STATE["imputer"]
    defaults = {feature: float(value) for feature, value in zip(selected_features, imputer.statistics_)}

    if request.method == "POST":
        payload = request.get_json(silent=True) or {}
        feature_payload = payload.get("features", {})

        clean_payload: dict[str, float] = {}
        for feature in selected_features:
            raw = feature_payload.get(feature, defaults[feature])
            try:
                clean_payload[feature] = float(raw)
            except (TypeError, ValueError):
                clean_payload[feature] = defaults[feature]

        prediction = save_user_profile(int(user_id), clean_payload)
        return jsonify({
            "message": "Profile saved.",
            "prediction": prediction,
        })

    context = get_user_profile_context(int(user_id))
    return jsonify({
        "features": selected_features,
        "defaults": defaults,
        "context": context,
    })


@app.route("/api/admin/user-insights")
def api_admin_user_insights() -> Any:
    return jsonify(build_admin_user_insights())


@app.route("/api/admin/send-campaign", methods=["POST"])
def api_admin_send_campaign() -> Any:
    payload = request.get_json(silent=True) or {}
    segment_label = str(payload.get("segment_label", "")).strip()
    subject = str(payload.get("subject", "")).strip()
    offer_text = str(payload.get("offer_text", "")).strip()
    discount_pct = float(payload.get("discount_pct", 0) or 0)

    if not subject or not offer_text:
        return jsonify({"error": "Subject and offer text are required."}), 400

    conn = get_user_db()
    try:
        query = """
            SELECT u.email
            FROM users u
            JOIN user_profiles p ON p.user_id = u.id
        """
        params: tuple[Any, ...] = ()
        if segment_label:
            query += " WHERE p.segment_label = ?"
            params = (segment_label,)

        recipients = conn.execute(query, params).fetchall()
        recipient_count = len(recipients)

        conn.execute(
            """
            INSERT INTO campaign_logs (segment_label, subject, offer_text, discount_pct, recipient_count, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                segment_label or "All Segments",
                subject,
                offer_text,
                discount_pct,
                recipient_count,
                utc_now_str(),
            ),
        )
        conn.commit()
    finally:
        conn.close()

    return jsonify({
        "message": f"Campaign queued for {recipient_count} users.",
        "recipientCount": recipient_count,
    })


@app.route("/api/health")
def health() -> Any:
    return jsonify({"status": "ok", "service": "SegmentIQ API"})


@app.route("/api/features")
def get_features() -> Any:
    selected_features: list[str] = MODEL_STATE["selected_features"]
    imputer: SimpleImputer = MODEL_STATE["imputer"]

    defaults = {feature: float(value) for feature, value in zip(selected_features, imputer.statistics_)}
    return jsonify({"features": selected_features, "defaults": defaults})


@app.route("/api/predict", methods=["POST"])
def predict_segment() -> Any:
    payload = request.get_json(silent=True) or {}
    raw_features = payload.get("features", {})

    selected_features: list[str] = MODEL_STATE["selected_features"]
    imputer: SimpleImputer = MODEL_STATE["imputer"]
    scaler: StandardScaler = MODEL_STATE["scaler"]
    kmeans: KMeans = MODEL_STATE["kmeans"]
    rf_model: RandomForestClassifier = MODEL_STATE["rf_model"]
    outlier_bounds: dict[str, dict[str, float]] = MODEL_STATE.get("outlier_bounds", {})

    row = {}
    for idx, feature in enumerate(selected_features):
        value = raw_features.get(feature, np.nan)
        try:
            row[feature] = float(value)
        except (TypeError, ValueError):
            row[feature] = float(imputer.statistics_[idx])

    input_df = pd.DataFrame([row], columns=selected_features)
    input_df = apply_outlier_bounds(input_df, outlier_bounds)
    input_imputed = imputer.transform(input_df)
    input_imputed_df = pd.DataFrame(input_imputed, columns=selected_features)
    input_scaled = scaler.transform(input_imputed)
    explanation = build_prediction_explanation(
        input_imputed_df=input_imputed_df,
        selected_features=selected_features,
        imputer=imputer,
        scaler=scaler,
        rf_model=rf_model,
    )

    rf_segment = int(rf_model.predict(input_imputed_df)[0])
    kmeans_segment = int(kmeans.predict(input_scaled)[0])

    segment_profile = MODEL_STATE.get("segment_profile", pd.DataFrame())
    names = build_segment_names(segment_profile)
    segment_name = names.get(rf_segment, f"Segment {rf_segment}")
    rec = get_recommendation_actions(segment_name)

    return jsonify(
        {
            "rfSegment": rf_segment,
            "kmeansSegment": kmeans_segment,
            "segmentName": segment_name,
            "message": f"Segment {rf_segment} - {segment_name}",
            "recommendation": rec["headline"],
            "recommendedActions": rec["actions"],
            "explainability": explanation,
        }
    )


@app.route("/api/dashboard")
def dashboard() -> Any:
    payload = make_dashboard_payload(MODEL_STATE)
    return jsonify(payload)


@app.route("/api/kpis")
def kpis() -> Any:
    return jsonify(build_business_kpi_layer(MODEL_STATE))


@app.route("/api/business-dashboard")
def business_dashboard() -> Any:
    return jsonify(build_business_decision_dashboard(MODEL_STATE))


@app.route("/api/nova-chat", methods=["POST"])
def nova_chat() -> Any:
    payload = request.get_json(silent=True) or {}
    question = str(payload.get("question", "")).strip()
    if not question:
        return jsonify({"answer": "Please ask a business question for NOVA."}), 400

    answer = groq_response(question, MODEL_STATE)
    if not answer:
        answer = rule_based_nova(question, MODEL_STATE)

    return jsonify({"answer": answer})


@app.route("/api/nova-status")
def nova_status() -> Any:
    return jsonify(get_nova_status())


@app.route("/api/admin/upload", methods=["POST"])
def upload_dataset() -> Any:
    if "dataset" not in request.files:
        return jsonify({"error": "No dataset file received."}), 400

    file = request.files["dataset"]
    if file.filename == "":
        return jsonify({"error": "Empty filename."}), 400

    try:
        uploaded_df = pd.read_csv(file)
    except Exception as exc:
        return jsonify({"error": f"Uploaded file is not a valid CSV: {exc}"}), 400

    quality_report = dataset_quality_report(
        uploaded_df,
        reference_features=MODEL_STATE.get("selected_features", []),
    )

    file.stream.seek(0)
    file.save(DATASET_PATH)
    return jsonify({"message": f"Dataset uploaded to {DATASET_PATH.name}", "qualityReport": quality_report})


@app.route("/api/admin/retrain", methods=["POST"])
def retrain() -> Any:
    global MODEL_STATE

    payload = request.get_json(silent=True) or {}
    business_k = int(payload.get("business_k", 4))

    current_df = pd.read_csv(DATASET_PATH)
    quality_report = dataset_quality_report(
        current_df,
        reference_features=MODEL_STATE.get("selected_features", []),
    )
    if not quality_report["readyForRetrain"]:
        return jsonify({
            "error": "Dataset quality checks failed. Fix the warnings before retraining.",
            "qualityReport": quality_report,
        }), 400

    previous_profile = MODEL_STATE.get("segment_profile", pd.DataFrame()).copy()
    summary = train_pipeline(DATASET_PATH, business_k=business_k)
    MODEL_STATE = load_artifacts()

    retrain_insights = build_retrain_insight(previous_profile, MODEL_STATE.get("segment_profile", pd.DataFrame()))

    return jsonify({"message": "Retraining completed.", "summary": summary, "retrainInsights": retrain_insights})


@app.route("/api/algorithm-fit")
def algorithm_fit() -> Any:
    return jsonify(dataset_algorithm_fit())


@app.route("/api/admin/data-quality")
def admin_data_quality() -> Any:
    current_df = pd.read_csv(DATASET_PATH)
    return jsonify(
        dataset_quality_report(
            current_df,
            reference_features=MODEL_STATE.get("selected_features", []),
        )
    )


def bootstrap() -> None:
    global MODEL_STATE

    init_user_portal_db()

    if not (ARTIFACT_DIR / "rf_model.joblib").exists():
        train_pipeline(DATASET_PATH, business_k=4)

    MODEL_STATE = load_artifacts()


bootstrap()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=True)
