#!/usr/bin/env python3
"""
Create DynamoDB tables for JalPriority AI Member B.
Idempotently creates five tables with on-demand billing:
- jp_b_plans
- jp_b_ledger
- jp_b_events
- jp_b_audit
- jp_b_weights
"""

import os
import sys
import boto3
from botocore.exceptions import ClientError

def get_dynamodb_resource():
    region = os.environ.get("B_AWS_REGION", "ap-south-1")
    endpoint_url = os.environ.get("B_DDB_ENDPOINT_URL")
    return boto3.resource("dynamodb", region_name=region, endpoint_url=endpoint_url)

def create_tables(ddb=None):
    if ddb is None:
        ddb = get_dynamodb_resource()

    prefix = os.environ.get("B_DDB_TABLE_PREFIX", "jp_b_")

    table_definitions = [
        {
            "TableName": f"{prefix}plans",
            "KeySchema": [
                {"AttributeName": "plan_id", "KeyType": "HASH"}
            ],
            "AttributeDefinitions": [
                {"AttributeName": "plan_id", "AttributeType": "S"},
                {"AttributeName": "scenario_id", "AttributeType": "S"},
                {"AttributeName": "created_at", "AttributeType": "S"},
            ],
            "GlobalSecondaryIndexes": [
                {
                    "IndexName": "idx_plans_scenario",
                    "KeySchema": [
                        {"AttributeName": "scenario_id", "KeyType": "HASH"},
                        {"AttributeName": "created_at", "KeyType": "RANGE"}
                    ],
                    "Projection": {"ProjectionType": "ALL"}
                }
            ],
            "BillingMode": "PAY_PER_REQUEST"
        },
        {
            "TableName": f"{prefix}ledger",
            "KeySchema": [
                {"AttributeName": "pk", "KeyType": "HASH"},
                {"AttributeName": "sk", "KeyType": "RANGE"}
            ],
            "AttributeDefinitions": [
                {"AttributeName": "pk", "AttributeType": "S"},
                {"AttributeName": "sk", "AttributeType": "N"},
                {"AttributeName": "service_area_id", "AttributeType": "S"},
            ],
            "GlobalSecondaryIndexes": [
                {
                    "IndexName": "idx_ledger_service_area",
                    "KeySchema": [
                        {"AttributeName": "service_area_id", "KeyType": "HASH"},
                        {"AttributeName": "sk", "KeyType": "RANGE"}
                    ],
                    "Projection": {"ProjectionType": "ALL"}
                }
            ],
            "BillingMode": "PAY_PER_REQUEST"
        },
        {
            "TableName": f"{prefix}events",
            "KeySchema": [
                {"AttributeName": "event_id", "KeyType": "HASH"}
            ],
            "AttributeDefinitions": [
                {"AttributeName": "event_id", "AttributeType": "S"},
                {"AttributeName": "scenario_id", "AttributeType": "S"},
                {"AttributeName": "ts", "AttributeType": "S"},
            ],
            "GlobalSecondaryIndexes": [
                {
                    "IndexName": "idx_events_scenario_ts",
                    "KeySchema": [
                        {"AttributeName": "scenario_id", "KeyType": "HASH"},
                        {"AttributeName": "ts", "KeyType": "RANGE"}
                    ],
                    "Projection": {"ProjectionType": "ALL"}
                }
            ],
            "BillingMode": "PAY_PER_REQUEST"
        },
        {
            "TableName": f"{prefix}audit",
            "KeySchema": [
                {"AttributeName": "audit_id", "KeyType": "HASH"}
            ],
            "AttributeDefinitions": [
                {"AttributeName": "audit_id", "AttributeType": "S"},
                {"AttributeName": "plan_id", "AttributeType": "S"},
                {"AttributeName": "ts", "AttributeType": "S"},
            ],
            "GlobalSecondaryIndexes": [
                {
                    "IndexName": "idx_audit_plan_ts",
                    "KeySchema": [
                        {"AttributeName": "plan_id", "KeyType": "HASH"},
                        {"AttributeName": "ts", "KeyType": "RANGE"}
                    ],
                    "Projection": {"ProjectionType": "ALL"}
                }
            ],
            "BillingMode": "PAY_PER_REQUEST"
        },
        {
            "TableName": f"{prefix}weights",
            "KeySchema": [
                {"AttributeName": "service_area_id", "KeyType": "HASH"}
            ],
            "AttributeDefinitions": [
                {"AttributeName": "service_area_id", "AttributeType": "S"}
            ],
            "BillingMode": "PAY_PER_REQUEST"
        }
    ]

    client = ddb.meta.client
    existing_tables = client.list_tables().get("TableNames", [])

    created = []
    skipped = []

    for tdef in table_definitions:
        tname = tdef["TableName"]
        if tname in existing_tables:
            print(f"Table '{tname}' already exists — skipping.")
            skipped.append(tname)
            continue

        print(f"Creating table '{tname}' with PAY_PER_REQUEST...")
        ddb.create_table(**tdef)
        created.append(tname)

    return {"created": created, "skipped": skipped}

if __name__ == "__main__":
    res = create_tables()
    print("Done. Created:", res["created"], "Skipped:", res["skipped"])
