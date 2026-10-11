import os
from typing import Optional, List, Dict, Any
import boto3
from boto3.dynamodb.conditions import Key, Attr
import orjson

from jp_b.config import settings
from jp_b.repositories.base import (
    PlansRepository,
    LedgerRepository,
    EventsRepository,
    AuditRepository,
    WeightsRepository,
)

def get_dynamodb_resource():
    region = getattr(settings, "b_aws_region", "ap-south-1")
    endpoint_url = os.environ.get("B_DDB_ENDPOINT_URL")
    return boto3.resource("dynamodb", region_name=region, endpoint_url=endpoint_url)

class DynamoDBPlansRepository(PlansRepository):
    def __init__(self, ddb=None):
        self.ddb = ddb or get_dynamodb_resource()
        prefix = getattr(settings, "b_ddb_table_prefix", "jp_b_")
        self.table = self.ddb.Table(f"{prefix}plans")

    def save(self, plan: dict) -> None:
        item = {
            "plan_id": plan["plan_id"],
            "scenario_id": plan.get("scenario_id", ""),
            "created_at": plan.get("created_at", ""),
            "status": plan.get("status", ""),
            "data": orjson.dumps(plan).decode("utf-8"),
        }
        self.table.put_item(Item=item)

    def get(self, plan_id: str) -> Optional[dict]:
        resp = self.table.get_item(Key={"plan_id": plan_id})
        item = resp.get("Item")
        if not item:
            return None
        return orjson.loads(item["data"])

    def list_by_scenario(
        self,
        scenario_id: Optional[str] = None,
        status: Optional[str] = None,
        latest: bool = False,
    ) -> List[dict]:
        if scenario_id:
            kwargs = {
                "IndexName": "idx_plans_scenario",
                "KeyConditionExpression": Key("scenario_id").eq(scenario_id),
                "ScanIndexForward": False,  # Sort created_at descending
            }
            if status:
                kwargs["FilterExpression"] = Attr("status").eq(status)
            if latest and not status:
                kwargs["Limit"] = 1

            resp = self.table.query(**kwargs)
            items = resp.get("Items", [])
        else:
            kwargs = {}
            if status:
                kwargs["FilterExpression"] = Attr("status").eq(status)
            resp = self.table.scan(**kwargs)
            items = resp.get("Items", [])
            items.sort(key=lambda x: x.get("created_at", ""), reverse=True)

        if latest and len(items) > 1:
            items = items[:1]

        return [orjson.loads(it["data"]) for it in items]

class DynamoDBLedgerRepository(LedgerRepository):
    def __init__(self, ddb=None):
        self.ddb = ddb or get_dynamodb_resource()
        prefix = getattr(settings, "b_ddb_table_prefix", "jp_b_")
        self.table = self.ddb.Table(f"{prefix}ledger")

    def save(self, service_area_id: str, area_id: str, window_index: int, data: dict) -> None:
        pk = f"{service_area_id}#{area_id}"
        item = {
            "pk": pk,
            "sk": int(window_index),
            "service_area_id": service_area_id,
            "data": orjson.dumps(data).decode("utf-8"),
        }
        self.table.put_item(Item=item)

    def get_all_for_service_area(self, service_area_id: str) -> List[dict]:
        resp = self.table.query(
            IndexName="idx_ledger_service_area",
            KeyConditionExpression=Key("service_area_id").eq(service_area_id),
        )
        return [orjson.loads(it["data"]) for it in resp.get("Items", [])]

    def replace_window(self, service_area_id: str, window_index: int, rows: List[dict]) -> None:
        # 1. Query existing rows for this window
        resp = self.table.query(
            IndexName="idx_ledger_service_area",
            KeyConditionExpression=Key("service_area_id").eq(service_area_id) & Key("sk").eq(int(window_index)),
        )
        # 2. Delete existing
        for it in resp.get("Items", []):
            self.table.delete_item(Key={"pk": it["pk"], "sk": it["sk"]})

        # 3. Write new rows
        for row in rows:
            pk = f"{service_area_id}#{row['area_id']}"
            self.table.put_item(
                Item={
                    "pk": pk,
                    "sk": int(window_index),
                    "service_area_id": service_area_id,
                    "data": orjson.dumps(row).decode("utf-8"),
                }
            )

class DynamoDBEventsRepository(EventsRepository):
    def __init__(self, ddb=None):
        self.ddb = ddb or get_dynamodb_resource()
        prefix = getattr(settings, "b_ddb_table_prefix", "jp_b_")
        self.table = self.ddb.Table(f"{prefix}events")

    def save(self, event: dict) -> None:
        item = {
            "event_id": event["event_id"],
            "scenario_id": event.get("scenario_id") or "none",
            "ts": event.get("ts", ""),
            "name": event.get("name", ""),
            "data": orjson.dumps(event).decode("utf-8"),
        }
        self.table.put_item(Item=item)

    def list_events(
        self,
        scenario_id: Optional[str] = None,
        name: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> List[dict]:
        if scenario_id:
            kwargs = {
                "IndexName": "idx_events_scenario_ts",
                "KeyConditionExpression": Key("scenario_id").eq(scenario_id),
                "ScanIndexForward": False,  # Sort ts descending
            }
            if name:
                kwargs["FilterExpression"] = Attr("name").eq(name)
            if limit:
                kwargs["Limit"] = limit
            resp = self.table.query(**kwargs)
            items = resp.get("Items", [])
        else:
            kwargs = {}
            if name:
                kwargs["FilterExpression"] = Attr("name").eq(name)
            resp = self.table.scan(**kwargs)
            items = resp.get("Items", [])
            items.sort(key=lambda x: x.get("ts", ""), reverse=True)

        if limit and len(items) > limit:
            items = items[:limit]

        return [orjson.loads(it["data"]) for it in items]

class DynamoDBAuditRepository(AuditRepository):
    def __init__(self, ddb=None):
        self.ddb = ddb or get_dynamodb_resource()
        prefix = getattr(settings, "b_ddb_table_prefix", "jp_b_")
        self.table = self.ddb.Table(f"{prefix}audit")

    def save(self, entry: dict) -> None:
        item = {
            "audit_id": entry["audit_id"],
            "ts": entry.get("ts", ""),
            "plan_id": entry.get("plan_id") or "none",
            "data": orjson.dumps(entry).decode("utf-8"),
        }
        self.table.put_item(Item=item)

    def list_entries(self, plan_id: Optional[str] = None, limit: int = 50) -> List[dict]:
        if plan_id:
            resp = self.table.query(
                IndexName="idx_audit_plan_ts",
                KeyConditionExpression=Key("plan_id").eq(plan_id),
                ScanIndexForward=False,
                Limit=limit,
            )
            items = resp.get("Items", [])
        else:
            resp = self.table.scan()
            items = resp.get("Items", [])
            items.sort(key=lambda x: x.get("ts", ""), reverse=True)

        if len(items) > limit:
            items = items[:limit]

        return [orjson.loads(it["data"]) for it in items]

class DynamoDBWeightsRepository(WeightsRepository):
    def __init__(self, ddb=None):
        self.ddb = ddb or get_dynamodb_resource()
        prefix = getattr(settings, "b_ddb_table_prefix", "jp_b_")
        self.table = self.ddb.Table(f"{prefix}weights")

    def save(self, service_area_id: str, data: dict) -> None:
        item = {
            "service_area_id": service_area_id,
            "data": orjson.dumps(data).decode("utf-8"),
        }
        self.table.put_item(Item=item)

    def get(self, service_area_id: str) -> Optional[dict]:
        resp = self.table.get_item(Key={"service_area_id": service_area_id})
        item = resp.get("Item")
        if not item:
            return None
        return orjson.loads(item["data"])
