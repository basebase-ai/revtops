"""
Workstream clustering: HDBSCAN + UMAP + LLM labels for semantic Home.

Clusters shared conversations by embedding similarity, projects to 2D for layout,
and generates human-readable workstream labels. Persists to Workstream rows and
matches existing rows by Jaccard overlap so user-edited names survive recompute.
"""

import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import numpy as np
from anthropic import AsyncAnthropic
from sqlalchemy import select

from config import settings
from models.conversation import Conversation
from models.database import get_session
from models.workstream import Workstream

logger = logging.getLogger(__name__)

_JACCARD_MATCH_THRESHOLD = 0.5

_MIN_CLUSTER_SIZE = 2
_MULTI_CLUSTER_SIMILARITY_THRESHOLD = 0.75
_UMAP_RANDOM_STATE = 42
_MAX_LABEL_CONVERSATIONS = 20


async def _fetch_embeddings_for_window(
    organization_id: str,
    window_hours: int,
) -> tuple[list[str], np.ndarray]:
    """Fetch shared conversations with embeddings updated in the last window. Returns (ids, matrix)."""
    since = datetime.utcnow() - timedelta(hours=window_hours)
    async with get_session(organization_id=organization_id) as session:
        result = await session.execute(
            select(Conversation.id, Conversation.embedding)
            .where(
                Conversation.organization_id == organization_id,
                Conversation.scope == "shared",
                Conversation.embedding.isnot(None),
                Conversation.updated_at >= since,
            )
        )
        rows = result.all()

    ids: list[str] = []
    vectors: list[list[float]] = []
    for conv_id, emb in rows:
        if emb is not None and len(emb) == 1536:
            ids.append(str(conv_id))
            vectors.append(list(emb))

    if not vectors:
        return ids, np.array([]).reshape(0, 1536)

    matrix = np.array(vectors, dtype=np.float64)
    return ids, matrix


def _jaccard_similarity(a: set[str], b: set[str]) -> float:
    """Jaccard similarity |A ∩ B| / |A ∪ B|; 1.0 if both empty."""
    if not a and not b:
        return 1.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def _cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """Single pair cosine similarity (assumes norms > 0)."""
    na = np.linalg.norm(a)
    nb = np.linalg.norm(b)
    if na == 0 or nb == 0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


def _cluster_centroid(matrix: np.ndarray, indices: np.ndarray) -> np.ndarray:
    """Mean of rows at indices (L2-normalized for cosine)."""
    subset = matrix[indices]
    centroid = np.mean(subset, axis=0)
    norm = np.linalg.norm(centroid)
    if norm > 0:
        centroid = centroid / norm
    return centroid


async def _generate_cluster_labels(
    organization_id: str,
    cluster_conversations: list[dict[str, Any]],
) -> list[tuple[str, str]]:
    """Call Haiku to get (label, description) per cluster. cluster_conversations: list of {titles, summaries}."""
    if not cluster_conversations:
        return []

    client = AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
    results: list[tuple[str, str]] = []

    for i, cluster in enumerate(cluster_conversations):
        titles = cluster.get("titles", [])[: _MAX_LABEL_CONVERSATIONS]
        summaries = cluster.get("summaries", [])[: _MAX_LABEL_CONVERSATIONS]
        text = "Conversations:\n"
        for t, s in zip(titles, summaries):
            text += f"- Title: {t or '(untitled)'}\n  Summary: {s or '(none)'}\n"

        prompt = (
            "Generate a short workstream label (2-5 words) and a one-sentence description "
            "for this group of conversations. Return ONLY valid JSON with two keys: "
            '"label" and "description". No markdown.\n\n' + text
        )
        try:
            response = await client.messages.create(
                model=settings.ANTHROPIC_CHEAP_MODEL,
                max_tokens=150,
                messages=[{"role": "user", "content": prompt}],
            )
            raw: str = ""
            for block in response.content:
                if hasattr(block, "text") and block.text:
                    raw = block.text.strip()
                    break
            logger.info("Cluster %s raw response (%d blocks): %r", i, len(response.content), raw[:300] if raw else "(empty)")
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[-1] if "\n" in raw else raw[3:]
            if raw.endswith("```"):
                raw = raw[: raw.rfind("```")]
            raw = raw.strip()
            if not raw:
                logger.warning("Cluster %s: no text in response, block types: %s", i, [getattr(b, "type", "?") for b in response.content])
                results.append((f"Workstream {i + 1}", ""))
                continue
            parsed = json.loads(raw)
            label = parsed.get("label", f"Workstream {i + 1}")
            desc = parsed.get("description", "")
            results.append((label, desc))
        except Exception as e:
            logger.warning("Cluster label generation failed for cluster %s: %s", i, e)
            results.append((f"Workstream {i + 1}", ""))

    return results


async def _fetch_no_embedding_ids(
    organization_id: str,
    window_hours: int,
    exclude: set[str] | None = None,
) -> list[str]:
    """Fetch shared conversation IDs in the window that have no embedding but do have messages."""
    since = datetime.utcnow() - timedelta(hours=window_hours)
    async with get_session(organization_id=organization_id) as session:
        result = await session.execute(
            select(Conversation.id)
            .where(
                Conversation.organization_id == organization_id,
                Conversation.scope == "shared",
                Conversation.embedding.is_(None),
                Conversation.updated_at >= since,
                Conversation.message_count > 0,
            )
        )
        ids: list[str] = [str(row[0]) for row in result.all()]
    if exclude:
        ids = [cid for cid in ids if cid not in exclude]
    return ids


async def compute_workstream_clusters(
    organization_id: str,
    window_hours: int = 24,
) -> dict[str, Any]:
    """
    Compute workstream clusters for shared conversations in the time window.

    Returns a dict suitable for caching and API response:
      workstreams: list of { id, label, description, position: [x,y], conversation_ids }
      unclustered_ids: list of conversation ids (noise)
      conversation_positions: { conversation_id: [x, y] }
      computed_at: ISO timestamp
    """
    conv_ids, matrix = await _fetch_embeddings_for_window(organization_id, window_hours)

    if len(conv_ids) == 0:
        no_emb: list[str] = await _fetch_no_embedding_ids(organization_id, window_hours)
        return {
            "workstreams": [],
            "unclustered_ids": no_emb,
            "conversation_positions": {},
            "computed_at": datetime.now(timezone.utc).isoformat(),
        }

    if matrix.shape[0] < 2:
        no_emb = await _fetch_no_embedding_ids(organization_id, window_hours, exclude=set(conv_ids))
        return {
            "workstreams": [],
            "unclustered_ids": conv_ids + no_emb,
            "conversation_positions": {cid: [0.5, 0.5] for cid in conv_ids},
            "computed_at": datetime.now(timezone.utc).isoformat(),
        }

    import hdbscan
    import umap

    clusterer = hdbscan.HDBSCAN(min_cluster_size=_MIN_CLUSTER_SIZE)
    labels = clusterer.fit_predict(matrix)

    reducer = umap.UMAP(n_components=2, random_state=_UMAP_RANDOM_STATE, metric="cosine")
    positions_2d = reducer.fit_transform(matrix)

    # Normalize positions to [0, 1]
    min_ = positions_2d.min(axis=0)
    max_ = positions_2d.max(axis=0)
    range_ = max_ - min_
    range_[range_ == 0] = 1.0
    positions_2d = (positions_2d - min_) / range_

    conversation_positions: dict[str, list[float]] = {
        cid: positions_2d[i].tolist() for i, cid in enumerate(conv_ids)
    }

    unique_labels = sorted(set(labels) - {-1})
    n_clusters = len(unique_labels)

    # Multi-cluster membership: assign each conv to all clusters where sim to centroid > threshold
    matrix_norm = matrix / (np.linalg.norm(matrix, axis=1, keepdims=True) + 1e-10)
    centroids: dict[int, np.ndarray] = {}
    for lbl in unique_labels:
        indices = np.where(labels == lbl)[0]
        centroids[lbl] = _cluster_centroid(matrix, indices)
        centroids[lbl] = centroids[lbl] / (np.linalg.norm(centroids[lbl]) + 1e-10)

    cluster_id_to_conv_ids: dict[int, list[str]] = {lbl: [] for lbl in unique_labels}
    for i in range(len(conv_ids)):
        for lbl in unique_labels:
            sim = float(np.dot(matrix_norm[i], centroids[lbl]))
            if sim >= _MULTI_CLUSTER_SIMILARITY_THRESHOLD:
                cluster_id_to_conv_ids[lbl].append(conv_ids[i])

    # Build cluster list: conversation_ids + position (no id/label yet)
    clusters: list[dict[str, Any]] = []
    for lbl in unique_labels:
        cids = cluster_id_to_conv_ids[lbl]
        if not cids:
            continue
        positions = [positions_2d[conv_ids.index(cid)] for cid in cids]
        centroid_2d = np.mean(positions, axis=0).tolist()
        clusters.append({
            "conversation_ids": cids,
            "position": centroid_2d,
        })

    unclustered_ids = [conv_ids[i] for i in range(len(conv_ids)) if labels[i] == -1]

    # Load existing workstreams and match each cluster by Jaccard
    existing_list: list[Workstream] = []
    if clusters:
        async with get_session(organization_id=organization_id) as session:
            existing_result = await session.execute(
                select(Workstream).where(
                    Workstream.organization_id == uuid.UUID(organization_id),
                    Workstream.window_hours == window_hours,
                    Workstream.is_active.is_(True),
                )
            )
            existing_list = list(existing_result.scalars().all())

    cluster_match_ids: list[uuid.UUID | None] = []
    for cluster in clusters:
        c_set = set(cluster["conversation_ids"])
        best: tuple[float, uuid.UUID | None] = (0.0, None)
        for ex in existing_list:
            ex_set = {str(x) for x in (ex.conversation_ids or [])}
            j = _jaccard_similarity(c_set, ex_set)
            if j >= _JACCARD_MATCH_THRESHOLD and j > best[0]:
                best = (j, ex.id)
        cluster_match_ids.append(best[1])

    existing_by_id = {ex.id: ex for ex in existing_list}

    # Which clusters need an AI-generated label? (no match, or match with label_overridden=False)
    need_label_clusters: list[dict[str, Any]] = []
    need_label_indices: list[int] = []
    for i, (cluster, match_id) in enumerate(zip(clusters, cluster_match_ids)):
        if match_id is None or not existing_by_id[match_id].label_overridden:
            need_label_clusters.append(cluster)
            need_label_indices.append(i)

    # Fetch titles/summaries for label generation (only for clusters that need labels)
    cluster_conversations_for_labels: list[dict[str, Any]] = []
    async with get_session(organization_id=organization_id) as session:
        for cluster in need_label_clusters:
            titles: list[str] = []
            summaries: list[str] = []
            for cid in cluster["conversation_ids"]:
                conv = await session.get(Conversation, cid)
                if conv:
                    titles.append(conv.title or "")
                    summaries.append((conv.summary or "").strip())
            cluster_conversations_for_labels.append({"titles": titles, "summaries": summaries})

    label_tuples = await _generate_cluster_labels(organization_id, cluster_conversations_for_labels)
    label_by_need_index: dict[int, tuple[str, str]] = {
        need_label_indices[j]: label_tuples[j] for j in range(len(label_tuples))
    }

    # Upsert: update matched rows, insert new, deactivate unmatched existing
    matched_existing_ids: set[uuid.UUID] = set()
    workstream_out_tuples: list[tuple[str, str, str, list[float], list[str]]] = []
    org_uuid = uuid.UUID(organization_id)

    async with get_session(organization_id=organization_id) as session:
        for i, (cluster, match_id) in enumerate(zip(clusters, cluster_match_ids)):
            cids_uuid = [uuid.UUID(cid) for cid in cluster["conversation_ids"]]
            pos = cluster["position"]
            if match_id is not None:
                matched_existing_ids.add(match_id)
                row = await session.get(Workstream, match_id)
                if row:
                    row.conversation_ids = cids_uuid
                    row.position = pos
                    if i in label_by_need_index:
                        label, desc = label_by_need_index[i]
                        row.label = label
                        row.description = desc or ""
                    await session.flush()
                    workstream_out_tuples.append((
                        str(row.id),
                        row.label,
                        row.description or "",
                        row.position or [0.5, 0.5],
                        [str(cid) for cid in (row.conversation_ids or [])],
                    ))
                else:
                    label, desc = label_by_need_index.get(i, ("Workstream", ""))
                    new_ws = Workstream(
                        organization_id=org_uuid,
                        window_hours=window_hours,
                        label=label,
                        description=desc or "",
                        label_overridden=False,
                        conversation_ids=cids_uuid,
                        is_active=True,
                        position=pos,
                    )
                    session.add(new_ws)
                    await session.flush()
                    workstream_out_tuples.append((
                        str(new_ws.id),
                        new_ws.label,
                        new_ws.description or "",
                        new_ws.position or [0.5, 0.5],
                        [str(cid) for cid in new_ws.conversation_ids],
                    ))
            else:
                label, desc = label_by_need_index.get(i, ("Workstream", ""))
                new_ws = Workstream(
                    organization_id=org_uuid,
                    window_hours=window_hours,
                    label=label,
                    description=desc or "",
                    label_overridden=False,
                    conversation_ids=cids_uuid,
                    is_active=True,
                    position=pos,
                )
                session.add(new_ws)
                await session.flush()
                workstream_out_tuples.append((
                    str(new_ws.id),
                    new_ws.label,
                    new_ws.description or "",
                    new_ws.position or [0.5, 0.5],
                    [str(cid) for cid in new_ws.conversation_ids],
                ))

        for ex in existing_list:
            if ex.id not in matched_existing_ids:
                row = await session.get(Workstream, ex.id)
                if row:
                    row.is_active = False

        await session.commit()

    workstreams_out = [
        {"id": wid, "label": lbl, "description": desc, "position": pos, "conversation_ids": cids}
        for wid, lbl, desc, pos, cids in workstream_out_tuples
    ]

    clustered_set: set[str] = set()
    for ws in workstreams_out:
        for cid in ws["conversation_ids"]:
            clustered_set.add(cid)
    exclude_set: set[str] = clustered_set | set(unclustered_ids)
    no_emb_ids: list[str] = await _fetch_no_embedding_ids(organization_id, window_hours, exclude=exclude_set)
    unclustered_ids.extend(no_emb_ids)

    return {
        "workstreams": workstreams_out,
        "unclustered_ids": unclustered_ids,
        "conversation_positions": conversation_positions,
        "computed_at": datetime.now(timezone.utc).isoformat(),
    }
