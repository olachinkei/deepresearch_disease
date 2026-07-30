from __future__ import annotations

import argparse
import csv
import os
import sys
from collections import Counter
from typing import Any


def _client(project_path: str) -> Any:
    import weave

    return weave.init(
        project_path,
        settings={
            "implicitly_patch_integrations": False,
            "capture_code": False,
            "capture_system_info": False,
            "print_call_link": False,
        },
    )


def fetch_flagged_rows(client: Any, *, limit: int) -> list[dict[str, Any]]:
    """Filter on the server and project only non-content columns."""

    from weave.trace_server.agents.types import (
        AgentSortBy,
        AgentSpansQueryReq,
        AgentSpanValueRef,
    )
    from weave.trace_server.interface.query import Query

    query = Query(
        **{  # type: ignore[arg-type]
            "$expr": {
                "$and": [
                    {
                        "$eq": [
                            {"$getField": "operation_name"},
                            {"$literal": "invoke_agent"},
                        ]
                    },
                    {
                        "$or": [
                            {
                                "$gte": [
                                    {
                                        "$getField": (
                                            "custom_attrs_float.app.context_ratio"
                                        )
                                    },
                                    {"$literal": 0.8},
                                ]
                            },
                            {
                                "$gte": [
                                    {
                                        "$getField": (
                                            "custom_attrs_int.app.duplicate_query_count"
                                        )
                                    },
                                    {"$literal": 2},
                                ]
                            },
                            {
                                "$not": [
                                    {
                                        "$eq": [
                                            {
                                                "$getField": (
                                                    "custom_attrs_string.app.flags_csv"
                                                )
                                            },
                                            {"$literal": ""},
                                        ]
                                    }
                                ]
                            },
                        ]
                    },
                ]
            }
        }
    )
    result = client.server.agent_spans_query(
        AgentSpansQueryReq(
            project_id=f"{client.entity}/{client.project}",
            query=query,
            custom_attr_columns=[
                AgentSpanValueRef(
                    source="custom_attrs_float",
                    key="app.context_ratio",
                ),
                AgentSpanValueRef(
                    source="custom_attrs_int",
                    key="app.tool_count",
                ),
                AgentSpanValueRef(
                    source="custom_attrs_int",
                    key="app.duplicate_query_count",
                ),
                AgentSpanValueRef(
                    source="custom_attrs_string",
                    key="app.flags_csv",
                ),
            ],
            include_details=False,
            include_costs=False,
            sort_by=[AgentSortBy(field="started_at", direction="desc")],
            limit=limit,
        )
    )
    rows: list[dict[str, Any]] = []
    for span in result.spans:
        rows.append(
            {
                "trace_id": str(span.trace_id),
                "started_at": str(span.started_at),
                "context_ratio": span.custom_attrs_float.get("app.context_ratio"),
                "tool_count": span.custom_attrs_int.get("app.tool_count"),
                "duplicate_query_count": span.custom_attrs_int.get(
                    "app.duplicate_query_count"
                ),
                "flags": span.custom_attrs_string.get("app.flags_csv", ""),
            }
        )
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Server-filter and tabulate flagged Weave root traces."
    )
    parser.add_argument(
        "--project",
        default=(
            f"{os.environ.get('WANDB_ENTITY', '')}/{os.environ.get('WANDB_PROJECT', '')}"
        ),
    )
    parser.add_argument("--limit", type=int, default=1000)
    args = parser.parse_args()
    if "/" not in args.project or args.project.startswith("/"):
        raise SystemExit("--project entity/project is required")
    rows = fetch_flagged_rows(_client(args.project), limit=min(max(args.limit, 1), 5000))
    writer = csv.DictWriter(
        sys.stdout,
        fieldnames=[
            "trace_id",
            "started_at",
            "context_ratio",
            "tool_count",
            "duplicate_query_count",
            "flags",
        ],
    )
    writer.writeheader()
    writer.writerows(rows)
    counts = Counter(flag for row in rows for flag in row["flags"].split(",") if flag)
    print(f"# rows={len(rows)} flag_counts={dict(counts)}", file=sys.stderr)
