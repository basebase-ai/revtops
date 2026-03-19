"""
Workstream clustering: HDBSCAN + UMAP + LLM labels for semantic Home.

Clusters shared conversations by embedding similarity, projects to 2D for layout,
and generates human-readable workstream labels.
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

logger = logging.getLogger(__name__)

_MIN_CLUSTER_SIZE = 2
_MULTI_CLUSTER_SIMILARITY_THRESHOLD = 0.75
_UMAP_RANDOM_STATE = 42
_MAX_LABEL_CONVERSATIONS = 20


async def _fetch_embeddings_for_window(
    organization_id: str,
    window_hours: int,
) -> tuple[list[str], np.ndarray]:
    """Fetch shared conversations with embeddings updated in the last window. Returns (ids, matrix)."""
    since = datetime.now(timezone.utc) - timedelta(hours=window_hours)
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
            raw = response.content[0].text.strip() if response.content else "{}"
            parsed = json.loads(raw)
            label = parsed.get("label", f"Workstream {i + 1}")
            desc = parsed.get("description", "")
            results.append((label, desc))
        except Exception as e:
            logger.warning("Cluster label generation failed for cluster %s: %s", i, e)
            results.append((f"Workstream {i + 1}", ""))

    return results


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
        return {
            "workstreams": [],
            "unclustered_ids": [],
            "conversation_positions": {},
            "computed_at": datetime.now(timezone.utc).isoformat(),
        }

    if matrix.shape[0] < 2:
        return {
            "workstreams": [],
            "unclustered_ids": conv_ids,
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

    # Build workstream list with positions (centroid of 2D positions)
    workstreams: list[dict[str, Any]] = []
    for lbl in unique_labels:
        cids = cluster_id_to_conv_ids[lbl]
        if not cids:
            continue
        positions = [positions_2d[conv_ids.index(cid)] for cid in cids]
        centroid_2d = np.mean(positions, axis=0).tolist()
        workstreams.append({
            "id": str(uuid.uuid4()),
            "label": "",
            "description": "",
            "position": centroid_2d,
            "conversation_ids": cids,
        })

    # Fetch titles/summaries for label generation
    async with get_session(organization_id=organization_id) as session:
        cluster_conversations: list[dict[str, Any]] = []
        for ws in workstreams:
            titles: list[str] = []
            summaries: list[str] = []
            for cid in ws["conversation_ids"]:
                conv = await session.get(Conversation, cid)
                if conv:
                    titles.append(conv.title or "")
                    overall = ""
                    if conv.summary:
                        try:
                            parsed = json.loads(conv.summary)
                            overall = (parsed.get("overall") or "") if isinstance(parsed, dict) else ""
                        except (json.JSONDecodeError, TypeError):
                            pass
                    summaries.append(overall)
            cluster_conversations.append({"titles": titles, "summaries": summaries})

    label_tuples = await _generate_cluster_labels(organization_id, cluster_conversations)
    for ws, (label, desc) in zip(workstreams, label_tuples):
        ws["label"] = label
        ws["description"] = desc

    unclustered_ids = [conv_ids[i] for i in range(len(conv_ids)) if labels[i] == -1]

    return {
        "workstreams": workstreams,
        "unclustered_ids": unclustered_ids,
        "conversation_positions": conversation_positions,
        "computed_at": datetime.now(timezone.utc).isoformat(),
    }
