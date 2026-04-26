from __future__ import annotations

import logging
import math
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import Select, and_, delete, func, select, text
from sqlalchemy.dialects.postgresql import insert

from config import settings
from models.account import Account
from models.activity import Activity
from models.chat_message import ChatMessage
from models.contact import Contact
from models.conversation import Conversation
from models.database import get_admin_session
from models.organization import Organization
from models.topic_graph_snapshot import TopicGraphSnapshot

logger = logging.getLogger(__name__)

PARTIAL_WARNING = "Partial data: some sources failed"
_TOKEN_RE = re.compile(r"\b[A-Za-z][A-Za-z0-9_\-]{2,}\b")
_STOP = {"the", "and", "for", "with", "this", "that", "from", "you", "your", "are", "was"}


@dataclass
class CandidateDoc:
    source: str
    source_ref: str
    text: str
    event_time: datetime
    ingestion_time: datetime


def _utc_bounds(graph_date: date) -> tuple[datetime, datetime]:
    start = datetime.combine(graph_date, time.min).replace(tzinfo=timezone.utc)
    end = start + timedelta(days=1)
    return start, end


def _normalize_dt(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def select_watermark_time(source_event_time: datetime | None, ingestion_event_time: datetime | None) -> datetime | None:
    source_dt = _normalize_dt(source_event_time)
    ingestion_dt = _normalize_dt(ingestion_event_time)
    return source_dt or ingestion_dt


def _tokenize(text: str) -> list[str]:
    out: list[str] = []
    for m in _TOKEN_RE.finditer(text):
        token = m.group(0).lower()
        if token in _STOP:
            continue
        out.append(token)
    return out


def _similarity(a: str, b: str) -> float:
    aset, bset = set(a.split()), set(b.split())
    if not aset or not bset:
        return 0.0
    return len(aset & bset) / len(aset | bset)


def _canonicalize(raw_nodes: list[str], min_conf: float) -> dict[str, str]:
    ordered = sorted(set(n.strip() for n in raw_nodes if n.strip()))
    canonical: list[str] = []
    mapping: dict[str, str] = {}
    for node in ordered:
        chosen = node
        best = 0.0
        for c in canonical:
            score = _similarity(node.lower(), c.lower())
            if score > best:
                best = score
                chosen = c
        if best >= min_conf:
            mapping[node] = chosen
        else:
            canonical.append(node)
            mapping[node] = node
    return mapping


async def _collect_activity_docs(org_id: str, graph_date: date) -> list[CandidateDoc]:
    start, end = _utc_bounds(graph_date)
    async with get_admin_session() as session:
        rows = (
            await session.execute(
                select(
                    Activity.id,
                    Activity.source_system,
                    Activity.subject,
                    Activity.description,
                    Activity.activity_date,
                    Activity.synced_at,
                    Activity.updated_at,
                ).where(Activity.organization_id == UUID(org_id))
            )
        ).all()

    docs: list[CandidateDoc] = []
    for r in rows:
        source_time = _normalize_dt(r.activity_date)
        ingestion_time = _normalize_dt(r.synced_at) or _normalize_dt(r.updated_at)
        if ingestion_time is None:
            continue
        watermark = select_watermark_time(source_time, ingestion_time)
        if not (start <= watermark < end):
            continue
        text_blob = "\n".join(x for x in [r.subject or "", r.description or ""] if x).strip()
        if not text_blob:
            continue
        docs.append(
            CandidateDoc(
                source=r.source_system or "activity",
                source_ref=f"activity:{r.id}",
                text=text_blob,
                event_time=source_time or ingestion_time,
                ingestion_time=ingestion_time,
            )
        )
    return docs


async def _collect_slack_docs(org_id: str, graph_date: date) -> tuple[list[CandidateDoc], list[str]]:
    start, end = _utc_bounds(graph_date)
    async with get_admin_session() as session:
        rows = (
            await session.execute(
                select(
                    ChatMessage.id,
                    ChatMessage.content,
                    ChatMessage.content_blocks,
                    ChatMessage.created_at,
                    Conversation.source_channel_id,
                )
                .join(Conversation, Conversation.id == ChatMessage.conversation_id)
                .where(
                    ChatMessage.organization_id == UUID(org_id),
                    Conversation.source == "slack",
                )
            )
        ).all()
    docs: list[CandidateDoc] = []
    channels: list[str] = []
    for r in rows:
        created_at = _normalize_dt(r.created_at)
        if created_at is None or not (start <= created_at < end):
            continue
        parts: list[str] = []
        if isinstance(r.content_blocks, list):
            for b in r.content_blocks:
                if isinstance(b, dict) and b.get("type") == "text" and isinstance(b.get("text"), str):
                    parts.append(b["text"])
        if r.content:
            parts.append(r.content)
        text_blob = "\n".join(x for x in parts if x).strip()
        if text_blob:
            docs.append(
                CandidateDoc(
                    source="slack",
                    source_ref=f"chat_message:{r.id}",
                    text=text_blob,
                    event_time=created_at,
                    ingestion_time=created_at,
                )
            )
        if r.source_channel_id:
            channels.append(str(r.source_channel_id))
    return docs, channels


async def _seed_crm_nodes(org_id: str) -> tuple[list[str], list[str]]:
    async with get_admin_session() as session:
        contacts = (await session.execute(select(Contact.name).where(Contact.organization_id == UUID(org_id)))).all()
        accounts = (await session.execute(select(Account.name).where(Account.organization_id == UUID(org_id)))).all()
    return [c[0] for c in contacts if c[0]], [a[0] for a in accounts if a[0]]


def _heat(unique_doc_count: int, newest_at: datetime) -> float:
    uniq_w = settings.TOPIC_GRAPH_HEAT_UNIQUE_DOC_WEIGHT
    rec_w = settings.TOPIC_GRAPH_HEAT_RECENCY_WEIGHT
    half_life = max(float(settings.TOPIC_GRAPH_HEAT_RECENCY_HALF_LIFE_HOURS), 1.0)
    age_h = max((datetime.now(timezone.utc) - newest_at).total_seconds() / 3600.0, 0.0)
    recency = math.exp(-math.log(2) * age_h / half_life)
    return uniq_w * unique_doc_count + rec_w * recency


async def generate_topic_graph_for_org_day(org_id: str, graph_date: date) -> dict[str, Any]:
    logger.info("topic_graph.stage=ingest org_id=%s graph_date=%s", org_id, graph_date.isoformat())
    warnings: list[str] = []
    coverage: dict[str, Any] = {"sources": {}, "partial": False}
    docs: list[CandidateDoc] = []
    slack_channels: list[str] = []

    for source_name, fn in (("activities", _collect_activity_docs),):
        try:
            sd = await fn(org_id, graph_date)
            docs.extend(sd)
            coverage["sources"][source_name] = {"docs": len(sd), "status": "ok"}
        except Exception as exc:
            logger.exception("topic_graph.stage=ingest_failed source=%s org_id=%s", source_name, org_id)
            warnings.append(f"{source_name}:{exc}")
            coverage["sources"][source_name] = {"docs": 0, "status": "failed"}

    try:
        slack_docs, slack_channels = await _collect_slack_docs(org_id, graph_date)
        docs.extend(slack_docs)
        coverage["sources"]["slack"] = {"docs": len(slack_docs), "status": "ok"}
    except Exception as exc:
        warnings.append(f"slack:{exc}")
        coverage["sources"]["slack"] = {"docs": 0, "status": "failed"}

    try:
        crm_people, crm_companies = await _seed_crm_nodes(org_id)
        coverage["sources"]["crm"] = {"people": len(crm_people), "companies": len(crm_companies), "status": "ok"}
    except Exception as exc:
        warnings.append(f"crm:{exc}")
        crm_people, crm_companies = [], []
        coverage["sources"]["crm"] = {"people": 0, "companies": 0, "status": "failed"}

    logger.info("topic_graph.stage=extract org_id=%s docs=%d", org_id, len(docs))
    node_to_docs: dict[str, set[str]] = defaultdict(set)
    node_newest: dict[str, datetime] = {}
    evidence: dict[str, list[dict[str, Any]]] = defaultdict(list)

    extracted_nodes: list[str] = []
    for doc in docs:
        tokens = _tokenize(doc.text)
        top_tokens = tokens[:25]
        for token in top_tokens:
            extracted_nodes.append(token)
            node_to_docs[token].add(doc.source_ref)
            node_newest[token] = max(node_newest.get(token, doc.event_time), doc.event_time)
            evidence[token].append(
                {
                    "ref": doc.source_ref,
                    "source": doc.source,
                    "event_time": doc.event_time.isoformat(),
                    "snippet": doc.text[:400],
                    "relevance": doc.text.lower().count(token.lower()),
                }
            )

    raw_nodes = extracted_nodes + slack_channels + crm_people + crm_companies
    canonical = _canonicalize(raw_nodes, float(settings.TOPIC_GRAPH_FUZZY_MERGE_MIN_CONFIDENCE))

    merged_docs: dict[str, set[str]] = defaultdict(set)
    merged_newest: dict[str, datetime] = {}
    merged_evidence: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for node, refs in node_to_docs.items():
        cnode = canonical.get(node, node)
        merged_docs[cnode].update(refs)
        merged_newest[cnode] = max(merged_newest.get(cnode, node_newest.get(node, datetime.now(timezone.utc))), node_newest.get(node, datetime.now(timezone.utc)))
        merged_evidence[cnode].extend(evidence.get(node, []))

    for n in slack_channels + crm_people + crm_companies:
        cnode = canonical.get(n, n)
        merged_docs.setdefault(cnode, set())
        merged_newest.setdefault(cnode, datetime.now(timezone.utc))

    node_list = sorted(merged_docs.keys())
    nodes: list[dict[str, Any]] = []
    for i, node in enumerate(node_list):
        nodes.append(
            {
                "id": node,
                "label": node,
                "type": "entity",
                "heat": round(_heat(len(merged_docs[node]), merged_newest[node]), 6),
                "unique_doc_count": len(merged_docs[node]),
            }
        )

    edge_weights: dict[tuple[str, str], float] = defaultdict(float)
    doc_to_nodes: dict[str, set[str]] = defaultdict(set)
    for node, refs in merged_docs.items():
        for ref in refs:
            doc_to_nodes[ref].add(node)
    for nodeset in doc_to_nodes.values():
        ns = sorted(nodeset)
        for i in range(len(ns)):
            for j in range(i + 1, len(ns)):
                edge_weights[(ns[i], ns[j])] += 1.0
    edges = [{"source": a, "target": b, "weight": w} for (a, b), w in sorted(edge_weights.items())]

    coverage["partial"] = any(v.get("status") == "failed" for v in coverage["sources"].values())
    if coverage["partial"]:
        coverage["warning_text"] = PARTIAL_WARNING

    payload = {
        "nodes": nodes,
        "edges": edges,
        "evidence_by_node": merged_evidence,
    }
    metadata = {
        "coverage": coverage,
        "warnings": warnings,
        "status": "completed_partial" if coverage["partial"] else "completed",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "doc_count": len(docs),
    }

    await upsert_topic_graph_snapshot(org_id, graph_date, payload, metadata)
    logger.info("topic_graph.stage=persist org_id=%s graph_date=%s nodes=%d edges=%d", org_id, graph_date.isoformat(), len(nodes), len(edges))
    return {"status": metadata["status"], "nodes": len(nodes), "edges": len(edges)}


async def upsert_topic_graph_snapshot(org_id: str, graph_date: date, graph_payload: dict[str, Any], run_metadata: dict[str, Any]) -> None:
    lock_key = int(graph_date.strftime("%Y%m%d"))
    async with get_admin_session() as session:
        # deterministic lock ordering: org hash then date
        await session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:org_id), :lock_key)"), {"org_id": org_id, "lock_key": lock_key})
        stmt = insert(TopicGraphSnapshot).values(
            organization_id=UUID(org_id),
            graph_date=graph_date,
            graph_payload=graph_payload,
            run_metadata=run_metadata,
            status=run_metadata.get("status", "completed"),
        )
        stmt = stmt.on_conflict_do_update(
            constraint="uq_topic_graph_org_date",
            set_={
                "graph_payload": stmt.excluded.graph_payload,
                "run_metadata": stmt.excluded.run_metadata,
                "status": stmt.excluded.status,
                "updated_at": func.now(),
            },
        )
        await session.execute(stmt)
        await session.commit()


async def get_topic_graph_snapshot(org_id: str, graph_date: date) -> TopicGraphSnapshot | None:
    async with get_admin_session() as session:
        row = await session.execute(
            select(TopicGraphSnapshot).where(
                TopicGraphSnapshot.organization_id == UUID(org_id),
                TopicGraphSnapshot.graph_date == graph_date,
            )
        )
        return row.scalar_one_or_none()


def _rank_evidence(evidence_rows: list[dict[str, Any]], node_id: str) -> list[dict[str, Any]]:
    dedup: dict[str, dict[str, Any]] = {}
    for row in evidence_rows:
        key = str(row.get("ref"))
        if key not in dedup:
            dedup[key] = row
    rows = list(dedup.values())
    relevance_sorted = sorted(rows, key=lambda r: (-int(r.get("relevance", 0)), str(r.get("ref", ""))))[:5]
    recent_sorted = sorted(rows, key=lambda r: (str(r.get("event_time", "")), str(r.get("ref", ""))), reverse=True)
    out: list[dict[str, Any]] = []
    used: set[str] = set()
    for row in relevance_sorted + recent_sorted:
        ref = str(row.get("ref", ""))
        if ref in used:
            continue
        used.add(ref)
        out.append(row)
        if len(out) >= int(settings.TOPIC_GRAPH_SNIPPETS_PER_NODE):
            break
    return out


async def get_node_evidence(org_id: str, graph_date: date, node_id: str) -> list[dict[str, Any]]:
    snap = await get_topic_graph_snapshot(org_id, graph_date)
    if snap is None:
        return []
    evidence_map = snap.graph_payload.get("evidence_by_node", {}) if isinstance(snap.graph_payload, dict) else {}
    rows = evidence_map.get(node_id, []) if isinstance(evidence_map, dict) else []
    if not isinstance(rows, list):
        return []
    return _rank_evidence([r for r in rows if isinstance(r, dict)], node_id)


async def list_all_organization_ids() -> list[str]:
    async with get_admin_session() as session:
        rows = await session.execute(select(Organization.id))
        return [str(r[0]) for r in rows.all()]


async def cleanup_topic_graph_retention() -> int:
    keep_days = int(settings.TOPIC_GRAPH_RETENTION_DAYS)
    cutoff = datetime.now(timezone.utc).date() - timedelta(days=keep_days)
    async with get_admin_session() as session:
        res = await session.execute(delete(TopicGraphSnapshot).where(TopicGraphSnapshot.graph_date < cutoff))
        await session.commit()
        return int(res.rowcount or 0)


def iter_date_range(start: date, end: date) -> list[date]:
    out: list[date] = []
    curr = start
    while curr <= end:
        out.append(curr)
        curr = curr + timedelta(days=1)
    return out
