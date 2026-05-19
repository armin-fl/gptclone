import hashlib
import json
import math
import re
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
from typing import Any

import requests
from django.conf import settings
from django.db import transaction
from langfuse import get_client, propagate_attributes
from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI, OpenAIError
from requests import RequestException

from .model_runtime import managed_rag_model
from .models import KnowledgeChunk, KnowledgeDocument

try:
    from pymilvus import (
        Collection,
        CollectionSchema,
        DataType,
        FieldSchema,
        connections,
        utility,
    )
except ImportError:
    Collection = None
    CollectionSchema = None
    DataType = None
    FieldSchema = None
    connections = None
    utility = None


class RagError(RuntimeError):
    pass


@dataclass(frozen=True)
class RagHit:
    chunk: KnowledgeChunk
    score: float
    rerank_score: float | None = None


_MILVUS_ALIAS = "gptclone_rag"


def rag_is_enabled(value: bool | None = None) -> bool:
    if value is None:
        return bool(getattr(settings, "RAG_ENABLED", False))
    return bool(value)


def _capture_content() -> bool:
    return bool(getattr(settings, "RAG_LANGFUSE_CAPTURE_CONTENT", False))


def _metric_metadata(extra: dict | None = None) -> dict:
    metadata = {
        "provider": "rag",
        "vector_store": "milvus",
        "milvus_collection": settings.RAG_MILVUS_COLLECTION,
        "embedding_model": settings.RAG_EMBEDDING_MODEL,
        "embedding_dim": int(settings.RAG_EMBEDDING_DIM),
        "rerank_enabled": bool(getattr(settings, "RAG_RERANK_ENABLED", True)),
        "rerank_provider": str(getattr(settings, "RAG_RERANK_PROVIDER", "")),
        "rerank_model": settings.RAG_RERANK_MODEL,
        "capture_content": _capture_content(),
    }
    if extra:
        metadata.update({key: value for key, value in extra.items() if value is not None})
    return metadata


def _trace_metadata(metadata: dict | None = None, extra: dict | None = None) -> dict[str, str]:
    raw_metadata = _metric_metadata({**(metadata or {}), **(extra or {})})
    return {
        key: str(value)
        for key, value in raw_metadata.items()
        if value is not None
    }


@contextmanager
def _rag_trace(
    *,
    trace_name: str = "rag-retrieval",
    langfuse_session_id: str | None = None,
    langfuse_user_id: str | None = None,
    langfuse_metadata: dict | None = None,
    extra_metadata: dict | None = None,
):
    with propagate_attributes(
        trace_name=trace_name,
        tags=["rag", "milvus", str(settings.RAG_EMBEDDING_MODEL), str(settings.RAG_RERANK_MODEL)],
        session_id=langfuse_session_id,
        user_id=langfuse_user_id,
        metadata=_trace_metadata(langfuse_metadata, extra_metadata),
    ):
        yield


@contextmanager
def _observation(
    *,
    name: str,
    as_type: str = "span",
    input: Any = None,
    metadata: dict | None = None,
    model: str | None = None,
    model_parameters: dict | None = None,
    usage_details: dict | None = None,
):
    with get_client().start_as_current_observation(
        name=name,
        as_type=as_type,
        input=input,
        metadata=_metric_metadata(metadata),
        model=model,
        model_parameters=model_parameters,
        usage_details=usage_details,
    ) as observation:
        try:
            yield observation
        except Exception as exc:
            observation.update(
                level="ERROR",
                status_message=str(exc)[:500],
                metadata=_metric_metadata({**(metadata or {}), "error_type": exc.__class__.__name__}),
            )
            raise


def _text_observation_payload(text: str) -> dict | str:
    if _capture_content():
        return text
    return {
        "chars": len(text),
        "sha256": _content_hash(text),
    }


def _texts_observation_payload(texts: list[str]) -> dict | list[str]:
    if _capture_content():
        return texts
    lengths = [len(text) for text in texts]
    return {
        "count": len(texts),
        "total_chars": sum(lengths),
        "min_chars": min(lengths) if lengths else 0,
        "max_chars": max(lengths) if lengths else 0,
        "sha256": [_content_hash(text) for text in texts[:20]],
    }


def _require_pymilvus() -> None:
    if Collection is None or connections is None or utility is None:
        raise RagError("pymilvus is not installed. Install backend requirements before using RAG.")


def _connect_milvus() -> None:
    _require_pymilvus()
    if connections.has_connection(_MILVUS_ALIAS):
        return

    token = str(getattr(settings, "RAG_MILVUS_TOKEN", ""))
    kwargs: dict[str, Any] = {
        "alias": _MILVUS_ALIAS,
        "uri": settings.RAG_MILVUS_URI,
    }
    if token:
        kwargs["token"] = token
    connections.connect(**kwargs)


def _collection_name() -> str:
    return str(settings.RAG_MILVUS_COLLECTION)


def ensure_milvus_collection():
    with _observation(
        name="rag.milvus.collection",
        metadata={
            "operation": "ensure_collection",
            "collection": _collection_name(),
            "embedding_dim": int(settings.RAG_EMBEDDING_DIM),
        },
    ) as observation:
        _connect_milvus()
        name = _collection_name()
        if utility.has_collection(name, using=_MILVUS_ALIAS):
            collection = Collection(name=name, using=_MILVUS_ALIAS)
            collection.load()
            observation.update(output={"created": False, "loaded": True})
            return collection

        schema = CollectionSchema(
            fields=[
                FieldSchema(name="id", dtype=DataType.VARCHAR, is_primary=True, max_length=64),
                FieldSchema(name="user_id", dtype=DataType.VARCHAR, max_length=64),
                FieldSchema(name="document_id", dtype=DataType.VARCHAR, max_length=64),
                FieldSchema(name="chunk_index", dtype=DataType.INT64),
                FieldSchema(
                    name="content",
                    dtype=DataType.VARCHAR,
                    max_length=int(settings.RAG_MILVUS_CONTENT_MAX_LENGTH),
                ),
                FieldSchema(
                    name="embedding",
                    dtype=DataType.FLOAT_VECTOR,
                    dim=int(settings.RAG_EMBEDDING_DIM),
                ),
            ],
            description="GPTClone RAG knowledge chunks",
            enable_dynamic_field=False,
        )
        collection = Collection(name=name, schema=schema, using=_MILVUS_ALIAS)
        collection.create_index(
            field_name="embedding",
            index_params={
                "index_type": "HNSW",
                "metric_type": "COSINE",
                "params": {"M": 16, "efConstruction": 200},
            },
        )
        collection.load()
        observation.update(output={"created": True, "loaded": True})
        return collection


def _escape_expr_value(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _milvus_document_expr(document_id: str) -> str:
    return f'document_id == "{_escape_expr_value(document_id)}"'


def _milvus_user_expr(user_id: str) -> str:
    return f'user_id == "{_escape_expr_value(user_id)}"'


def _normalize_document_text(content: str) -> str:
    content = content.replace("\r\n", "\n").replace("\r", "\n")
    content = re.sub(r"[ \t]+", " ", content)
    content = re.sub(r"\n{4,}", "\n\n\n", content)
    return content.strip()


def _content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _title_from_content(content: str) -> str:
    first_line = next((line.strip() for line in content.splitlines() if line.strip()), "")
    clean = " ".join(first_line.split())
    if len(clean) > 80:
        clean = f"{clean[:77]}..."
    return clean or "Knowledge document"


def chunk_text(content: str) -> list[str]:
    text = _normalize_document_text(content)
    if not text:
        return []

    max_chars = max(200, int(settings.RAG_CHUNK_CHARS))
    overlap_chars = max(0, min(int(settings.RAG_CHUNK_OVERLAP_CHARS), max_chars // 2))
    chunks: list[str] = []
    start = 0
    text_length = len(text)

    while start < text_length:
        end = min(text_length, start + max_chars)
        if end < text_length:
            minimum_break = start + max(80, max_chars // 2)
            break_at = -1
            for separator in ("\n\n", "\n", ". ", " "):
                candidate = text.rfind(separator, minimum_break, end)
                if candidate > break_at:
                    break_at = candidate + len(separator)
            if break_at > start:
                end = break_at

        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= text_length:
            break
        start = max(end - overlap_chars, start + 1)

    max_chunks = int(settings.RAG_MAX_CHUNKS_PER_DOCUMENT)
    if len(chunks) > max_chunks:
        raise RagError(f"Document produced {len(chunks)} chunks; the configured limit is {max_chunks}.")
    return chunks


def _embedding_client() -> OpenAI:
    return OpenAI(
        api_key=settings.RAG_EMBEDDING_API_KEY,
        base_url=str(settings.RAG_EMBEDDING_BASE_URL).rstrip("/"),
        timeout=settings.RAG_EMBEDDING_TIMEOUT_SECONDS,
    )


def _normalize_vector(vector: list[float]) -> list[float]:
    length = math.sqrt(sum(value * value for value in vector))
    if length == 0:
        return vector
    return [value / length for value in vector]


def embed_texts(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []

    vectors: list[list[float]] = []
    batch_size = max(1, int(settings.RAG_EMBEDDING_BATCH_SIZE))
    extra_kwargs: dict[str, Any] = {}
    if getattr(settings, "RAG_EMBEDDING_REQUEST_DIMENSIONS", False):
        extra_kwargs["dimensions"] = int(settings.RAG_EMBEDDING_DIM)

    with _observation(
        name="rag.embedding",
        as_type="embedding",
        input=_texts_observation_payload(texts),
        model=settings.RAG_EMBEDDING_MODEL,
        model_parameters={
            "batch_size": batch_size,
            "dimensions": int(settings.RAG_EMBEDDING_DIM),
            "normalize": bool(getattr(settings, "RAG_NORMALIZE_EMBEDDINGS", True)),
        },
        metadata={
            "operation": "embed_texts",
            "text_count": len(texts),
            "total_chars": sum(len(text) for text in texts),
            "base_url": str(settings.RAG_EMBEDDING_BASE_URL).rstrip("/"),
        },
        usage_details={
            "input": len(texts),
            "input_chars": sum(len(text) for text in texts),
        },
    ) as observation:
        try:
            with managed_rag_model(settings.RAG_EMBEDDING_MODEL):
                client = _embedding_client()
                for start in range(0, len(texts), batch_size):
                    batch = texts[start : start + batch_size]
                    response = client.embeddings.create(
                        model=settings.RAG_EMBEDDING_MODEL,
                        input=batch,
                        **extra_kwargs,
                    )
                    response_items = sorted(response.data, key=lambda item: item.index)
                    for item in response_items:
                        vector = [float(value) for value in item.embedding]
                        if len(vector) != int(settings.RAG_EMBEDDING_DIM):
                            raise RagError(
                                "Embedding dimension mismatch: "
                                f"expected {settings.RAG_EMBEDDING_DIM}, got {len(vector)}."
                            )
                        if getattr(settings, "RAG_NORMALIZE_EMBEDDINGS", True):
                            vector = _normalize_vector(vector)
                        vectors.append(vector)
        except OpenAIError as exc:
            endpoint = f"{str(settings.RAG_EMBEDDING_BASE_URL).rstrip('/')}/embeddings"
            if isinstance(exc, APIStatusError):
                details = getattr(exc.response, "text", "") or str(exc)
                raise RagError(f"Embedding HTTP error {exc.status_code} at {endpoint}: {details}") from exc
            if isinstance(exc, (APIConnectionError, APITimeoutError)):
                raise RagError(f"Could not reach embedding model at {endpoint}: {exc}") from exc
            raise RagError(f"Embedding request failed: {exc}") from exc

    if len(vectors) != len(texts):
        raise RagError(f"Embedding response count mismatch: expected {len(texts)}, got {len(vectors)}.")
    observation.update(
        output={
            "vector_count": len(vectors),
            "embedding_dim": len(vectors[0]) if vectors else 0,
        },
        usage_details={
            "input": len(texts),
            "input_chars": sum(len(text) for text in texts),
            "output": len(vectors),
        },
    )
    return vectors


def _insert_vectors(user_id: str, document_id: str, chunks: list[KnowledgeChunk], embeddings: list[list[float]]) -> None:
    with _observation(
        name="rag.milvus.insert",
        metadata={
            "operation": "insert_vectors",
            "document_id": document_id,
            "chunk_count": len(chunks),
            "embedding_count": len(embeddings),
            "embedding_dim": len(embeddings[0]) if embeddings else 0,
        },
    ) as observation:
        collection = ensure_milvus_collection()
        max_content_length = int(settings.RAG_MILVUS_CONTENT_MAX_LENGTH)
        collection.insert(
            [
                [str(chunk.id) for chunk in chunks],
                [str(user_id) for _chunk in chunks],
                [str(document_id) for _chunk in chunks],
                [int(chunk.chunk_index) for chunk in chunks],
                [chunk.content[:max_content_length] for chunk in chunks],
                embeddings,
            ]
        )
        collection.flush()
        observation.update(output={"inserted": len(chunks), "flushed": True})


def delete_document_vectors(document_id: str) -> None:
    with _observation(
        name="rag.milvus.delete",
        metadata={"operation": "delete_document_vectors", "document_id": document_id},
    ) as observation:
        _connect_milvus()
        name = _collection_name()
        if not utility.has_collection(name, using=_MILVUS_ALIAS):
            observation.update(output={"deleted": False, "reason": "collection_not_found"})
            return
        collection = Collection(name=name, using=_MILVUS_ALIAS)
        collection.delete(_milvus_document_expr(document_id))
        collection.flush()
        observation.update(output={"deleted": True, "flushed": True})


def index_knowledge_document(
    *,
    user,
    content: str,
    title: str = "",
    source_name: str = "",
    langfuse_user_id: str | None = None,
    langfuse_metadata: dict | None = None,
) -> KnowledgeDocument:
    normalized_content = _normalize_document_text(content)
    with _rag_trace(
        trace_name="rag-index-document",
        langfuse_user_id=langfuse_user_id or str(user.id),
        langfuse_metadata=langfuse_metadata,
        extra_metadata={
            "operation": "index_document",
            "content_chars": len(normalized_content),
            "source_name": source_name,
        },
    ):
        with _observation(
            name="rag.index_document",
            input=_text_observation_payload(normalized_content),
            metadata={
                "operation": "index_document",
                "title": title,
                "source_name": source_name,
                "content_chars": len(normalized_content),
            },
        ) as observation:
            chunks = chunk_text(normalized_content)
            if not chunks:
                raise RagError("Knowledge document content cannot be empty.")

            embeddings = embed_texts(chunks)
            title = " ".join((title or "").split()) or _title_from_content(normalized_content)
            source_name = " ".join((source_name or "").split())
            content_hash = _content_hash(normalized_content)

            with transaction.atomic():
                document = KnowledgeDocument.objects.create(
                    user=user,
                    title=title,
                    source_name=source_name,
                    content_hash=content_hash,
                    chunk_count=len(chunks),
                )
                chunk_rows = [
                    KnowledgeChunk(
                        document=document,
                        chunk_index=index,
                        content=chunk,
                        metadata={"source_name": source_name} if source_name else {},
                    )
                    for index, chunk in enumerate(chunks)
                ]
                KnowledgeChunk.objects.bulk_create(chunk_rows)
                _insert_vectors(
                    str(user.id),
                    str(document.id),
                    chunk_rows,
                    embeddings,
                )

            observation.update(
                output={
                    "document_id": str(document.id),
                    "title": document.title,
                    "chunk_count": len(chunks),
                    "content_hash": content_hash,
                    "embedding_count": len(embeddings),
                },
                metadata=_metric_metadata(
                    {
                        "document_id": str(document.id),
                        "chunk_count": len(chunks),
                        "content_hash": content_hash,
                    }
                ),
            )
            return document


def delete_knowledge_document(
    *,
    user,
    document_id: str,
    langfuse_user_id: str | None = None,
    langfuse_metadata: dict | None = None,
) -> bool:
    with _rag_trace(
        trace_name="rag-delete-document",
        langfuse_user_id=langfuse_user_id or str(user.id),
        langfuse_metadata=langfuse_metadata,
        extra_metadata={"operation": "delete_document", "document_id": document_id},
    ):
        with _observation(
            name="rag.delete_document",
            metadata={"operation": "delete_document", "document_id": document_id},
        ) as observation:
            document = KnowledgeDocument.objects.filter(id=document_id, user=user).first()
            if not document:
                observation.update(output={"deleted": False, "reason": "not_found"})
                return False

            delete_document_vectors(str(document.id))
            document.delete()
            observation.update(output={"deleted": True, "document_id": str(document.id)})
            return True


def _score_from_hit(hit) -> float:
    value = getattr(hit, "score", None)
    if value is None:
        value = getattr(hit, "distance", 0.0)
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _search_milvus(*, user_id: str, embedding: list[float], top_k: int) -> list[tuple[str, float]]:
    with _observation(
        name="rag.milvus.search",
        metadata={
            "operation": "vector_search",
            "top_k": int(top_k),
            "embedding_dim": len(embedding),
            "metric_type": "COSINE",
        },
    ) as observation:
        collection = ensure_milvus_collection()
        results = collection.search(
            data=[embedding],
            anns_field="embedding",
            param={"metric_type": "COSINE", "params": {"ef": 64}},
            limit=max(1, int(top_k)),
            expr=_milvus_user_expr(user_id),
            output_fields=["id"],
            consistency_level="Strong",
        )
        if not results:
            observation.update(output={"candidate_count": 0})
            return []

        matches: list[tuple[str, float]] = []
        for hit in results[0]:
            entity = getattr(hit, "entity", None)
            chunk_id = ""
            if entity is not None:
                chunk_id = str(entity.get("id") or "")
            if not chunk_id:
                chunk_id = str(getattr(hit, "id", ""))
            if chunk_id:
                matches.append((chunk_id, _score_from_hit(hit)))
        observation.update(
            output={
                "candidate_count": len(matches),
                "top_score": matches[0][1] if matches else None,
                "chunk_ids": [chunk_id for chunk_id, _score in matches[:20]],
            }
        )
        return matches


def _rerank_base_url() -> str:
    return str(settings.RAG_RERANK_BASE_URL).rstrip("/")


def _rerank_headers() -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if settings.RAG_RERANK_API_KEY:
        headers["Authorization"] = f"Bearer {settings.RAG_RERANK_API_KEY}"
    return headers


def _rerank_with_endpoint(query: str, hits: list[RagHit]) -> list[RagHit]:
    endpoint = f"{_rerank_base_url()}/rerank"
    payload = {
        "model": settings.RAG_RERANK_MODEL,
        "query": query,
        "documents": [hit.chunk.content[: int(settings.RAG_RERANK_MAX_DOCUMENT_CHARS)] for hit in hits],
        "top_n": len(hits),
    }
    with _observation(
        name="rag.rerank.endpoint",
        as_type="retriever",
        input={
            "query": _text_observation_payload(query),
            "candidate_count": len(hits),
            "chunk_ids": [str(hit.chunk.id) for hit in hits[:20]],
        },
        model=settings.RAG_RERANK_MODEL,
        metadata={
            "operation": "rerank_endpoint",
            "candidate_count": len(hits),
            "endpoint": endpoint,
        },
    ) as observation:
        try:
            with managed_rag_model(settings.RAG_RERANK_MODEL):
                response = requests.post(
                    endpoint,
                    headers=_rerank_headers(),
                    json=payload,
                    timeout=settings.RAG_RERANK_TIMEOUT_SECONDS,
                )
            response.raise_for_status()
            data = response.json()
        except (RequestException, ValueError) as exc:
            raise RagError(f"Rerank request failed at {endpoint}: {exc}") from exc

        scored_hits: list[RagHit] = []
        for result in data.get("results", data.get("data", [])):
            if not isinstance(result, dict):
                continue
            try:
                index = int(result.get("index"))
            except (TypeError, ValueError):
                continue
            if index < 0 or index >= len(hits):
                continue
            raw_score = result.get("relevance_score", result.get("score", 0.0))
            try:
                score = float(raw_score)
            except (TypeError, ValueError):
                score = 0.0
            hit = hits[index]
            scored_hits.append(RagHit(chunk=hit.chunk, score=hit.score, rerank_score=score))

        if not scored_hits:
            observation.update(output={"reranked_count": 0, "fallback": True})
            return hits
        reranked = sorted(scored_hits, key=lambda hit: hit.rerank_score or 0.0, reverse=True)
        observation.update(
            output={
                "reranked_count": len(reranked),
                "top_chunk_id": str(reranked[0].chunk.id) if reranked else "",
                "top_rerank_score": reranked[0].rerank_score if reranked else None,
            }
        )
        return reranked


def _rerank_chat_prompt(query: str, hits: list[RagHit]) -> str:
    documents = []
    max_chars = int(settings.RAG_RERANK_MAX_DOCUMENT_CHARS)
    for index, hit in enumerate(hits):
        documents.append(f"[{index}]\n{hit.chunk.content[:max_chars]}")
    return (
        "Score each document for how useful it is for answering the query. "
        "Return only compact JSON in this exact shape: "
        '{"scores":[{"index":0,"score":0.0}]} where score is between 0 and 1.\n\n'
        f"Query:\n{query}\n\nDocuments:\n\n" + "\n\n".join(documents)
    )


def _json_object_from_text(text: str) -> dict:
    clean = text.strip()
    if clean.startswith("```"):
        clean = re.sub(r"^```(?:json)?", "", clean).strip()
        clean = re.sub(r"```$", "", clean).strip()
    try:
        return json.loads(clean)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", clean, flags=re.DOTALL)
        if not match:
            raise
        return json.loads(match.group(0))


def _rerank_with_chat(query: str, hits: list[RagHit]) -> list[RagHit]:
    with _observation(
        name="rag.rerank.chat",
        as_type="generation",
        input={
            "query": _text_observation_payload(query),
            "candidate_count": len(hits),
            "chunk_ids": [str(hit.chunk.id) for hit in hits[:20]],
        },
        model=settings.RAG_RERANK_MODEL,
        model_parameters={"temperature": 0, "max_tokens": 1024},
        metadata={
            "operation": "rerank_chat",
            "candidate_count": len(hits),
            "base_url": _rerank_base_url(),
        },
    ) as observation:
        try:
            with managed_rag_model(settings.RAG_RERANK_MODEL):
                client = OpenAI(
                    api_key=settings.RAG_RERANK_API_KEY,
                    base_url=_rerank_base_url(),
                    timeout=settings.RAG_RERANK_TIMEOUT_SECONDS,
                )
                completion = client.chat.completions.create(
                    model=settings.RAG_RERANK_MODEL,
                    messages=[
                        {
                            "role": "system",
                            "content": "You are a precise relevance scoring reranker.",
                        },
                        {"role": "user", "content": _rerank_chat_prompt(query, hits)},
                    ],
                    temperature=0,
                    max_tokens=1024,
                )
        except OpenAIError as exc:
            raise RagError(f"Chat rerank request failed: {exc}") from exc

        choices = getattr(completion, "choices", None) or []
        content = ""
        if choices:
            content = str(getattr(getattr(choices[0], "message", None), "content", "") or "")
        try:
            payload = _json_object_from_text(content)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise RagError(f"Chat rerank returned non-JSON content: {content[:200]}") from exc

        scores_by_index: dict[int, float] = {}
        for item in payload.get("scores", []):
            if not isinstance(item, dict):
                continue
            try:
                index = int(item.get("index"))
                score = float(item.get("score"))
            except (TypeError, ValueError):
                continue
            if 0 <= index < len(hits):
                scores_by_index[index] = max(0.0, min(1.0, score))

        if not scores_by_index:
            observation.update(output={"reranked_count": 0, "fallback": True, "raw_output_chars": len(content)})
            return hits

        reranked = [
            RagHit(
                chunk=hit.chunk,
                score=hit.score,
                rerank_score=scores_by_index.get(index, 0.0),
            )
            for index, hit in enumerate(hits)
        ]
        reranked = sorted(reranked, key=lambda hit: hit.rerank_score or 0.0, reverse=True)
        observation.update(
            output={
                "reranked_count": len(reranked),
                "top_chunk_id": str(reranked[0].chunk.id) if reranked else "",
                "top_rerank_score": reranked[0].rerank_score if reranked else None,
                "scores": scores_by_index,
            }
        )
        return reranked


def rerank_hits(query: str, hits: list[RagHit]) -> list[RagHit]:
    if not hits or not getattr(settings, "RAG_RERANK_ENABLED", True):
        return hits

    provider = str(getattr(settings, "RAG_RERANK_PROVIDER", "chat")).lower()
    if provider in {"none", "off", "disabled"}:
        return hits
    if provider in {"vllm-rerank", "rerank", "endpoint"}:
        return _rerank_with_endpoint(query, hits)
    if provider == "chat":
        return _rerank_with_chat(query, hits)
    raise RagError(f"Unsupported RAG rerank provider '{provider}'.")


def retrieve_relevant_chunks(
    *,
    user,
    query: str,
    top_k: int | None = None,
    langfuse_session_id: str | None = None,
    langfuse_user_id: str | None = None,
    langfuse_metadata: dict | None = None,
) -> list[RagHit]:
    clean_query = query.strip()
    trace_context = (
        _rag_trace(
            trace_name="rag-search",
            langfuse_session_id=langfuse_session_id,
            langfuse_user_id=langfuse_user_id or str(user.id),
            langfuse_metadata=langfuse_metadata,
            extra_metadata={"operation": "search", "query_chars": len(clean_query), "top_k": top_k},
        )
        if langfuse_session_id or langfuse_user_id or langfuse_metadata
        else nullcontext()
    )
    with trace_context:
        with _observation(
            name="rag.retrieve",
            as_type="retriever",
            input=_text_observation_payload(clean_query),
            metadata={
                "operation": "retrieve",
                "query_chars": len(clean_query),
                "requested_top_k": top_k,
            },
        ) as observation:
            if not clean_query:
                observation.update(output={"hit_count": 0, "reason": "empty_query"})
                return []
            document_count = KnowledgeDocument.objects.filter(user=user).count()
            if document_count == 0:
                observation.update(output={"hit_count": 0, "reason": "no_documents"})
                return []

            final_top_k = int(top_k or settings.RAG_CONTEXT_TOP_K)
            retrieval_top_k = max(final_top_k, int(settings.RAG_RETRIEVAL_TOP_K))
            query_embedding = embed_texts([clean_query])[0]
            raw_matches = _search_milvus(
                user_id=str(user.id),
                embedding=query_embedding,
                top_k=retrieval_top_k,
            )
            if not raw_matches:
                observation.update(
                    output={
                        "hit_count": 0,
                        "candidate_count": 0,
                        "document_count": document_count,
                    }
                )
                return []

            chunk_ids = [chunk_id for chunk_id, _score in raw_matches]
            chunks_by_id = {
                str(chunk.id): chunk
                for chunk in KnowledgeChunk.objects.select_related("document").filter(
                    id__in=chunk_ids,
                    document__user=user,
                )
            }
            hits = [
                RagHit(chunk=chunks_by_id[chunk_id], score=score)
                for chunk_id, score in raw_matches
                if chunk_id in chunks_by_id
            ]
            if len(hits) > 1:
                hits = rerank_hits(clean_query, hits)
            hits = hits[:final_top_k]
            observation.update(
                output={
                    "hit_count": len(hits),
                    "candidate_count": len(raw_matches),
                    "document_count": document_count,
                    "final_top_k": final_top_k,
                    "retrieval_top_k": retrieval_top_k,
                    "top_score": hits[0].score if hits else None,
                    "top_rerank_score": hits[0].rerank_score if hits and hits[0].rerank_score is not None else None,
                    "sources": [
                        {
                            "document_id": str(hit.chunk.document_id),
                            "chunk_id": str(hit.chunk.id),
                            "chunk_index": hit.chunk.chunk_index,
                            "score": hit.score,
                            "rerank_score": hit.rerank_score,
                        }
                        for hit in hits
                    ],
                }
            )
            return hits


def serialize_rag_hit(hit: RagHit, *, rank: int) -> dict:
    document = hit.chunk.document
    return {
        "rank": rank,
        "chunk_id": str(hit.chunk.id),
        "document_id": str(document.id),
        "document_title": document.title,
        "source_name": document.source_name,
        "chunk_index": hit.chunk.chunk_index,
        "score": hit.score,
        "rerank_score": hit.rerank_score,
        "content": hit.chunk.content,
    }


def build_rag_context_message(hits: list[RagHit]) -> str:
    if not hits:
        return ""

    max_total_chars = int(settings.RAG_CONTEXT_MAX_CHARS)
    max_chunk_chars = int(settings.RAG_CONTEXT_CHUNK_MAX_CHARS)
    remaining = max_total_chars
    sections = [
        "Retrieved knowledge snippets. Use them when relevant, and cite them with [RAG-1], [RAG-2], etc. "
        "If the snippets do not answer the user, say what is missing instead of guessing."
    ]
    for index, hit in enumerate(hits, start=1):
        document = hit.chunk.document
        source = document.source_name or document.title
        snippet = hit.chunk.content.strip()[:max_chunk_chars]
        header = f"[RAG-{index}] {source} (chunk {hit.chunk.chunk_index + 1})"
        block = f"{header}\n{snippet}"
        if len(block) > remaining:
            break
        sections.append(block)
        remaining -= len(block)

    return "\n\n".join(sections)


def build_rag_prompt_context(
    *,
    user,
    query: str,
    enabled: bool | None = None,
    langfuse_session_id: str | None = None,
    langfuse_user_id: str | None = None,
    langfuse_metadata: dict | None = None,
) -> tuple[str, list[dict], str]:
    with _rag_trace(
        trace_name="rag-chat-context",
        langfuse_session_id=langfuse_session_id,
        langfuse_user_id=langfuse_user_id or str(user.id),
        langfuse_metadata=langfuse_metadata,
        extra_metadata={
            "operation": "build_chat_context",
            "enabled": rag_is_enabled(enabled),
            "query_chars": len(query.strip()),
        },
    ):
        with _observation(
            name="rag.build_chat_context",
            as_type="chain",
            input=_text_observation_payload(query.strip()),
            metadata={
                "operation": "build_chat_context",
                "enabled": rag_is_enabled(enabled),
                "fail_open": bool(getattr(settings, "RAG_FAIL_OPEN", True)),
            },
        ) as observation:
            if not rag_is_enabled(enabled):
                observation.update(output={"enabled": False, "hit_count": 0, "context_chars": 0})
                return "", [], ""

            try:
                hits = retrieve_relevant_chunks(user=user, query=query)
            except RagError as exc:
                if getattr(settings, "RAG_FAIL_OPEN", True):
                    error = str(exc)
                    observation.update(
                        output={"hit_count": 0, "context_chars": 0, "error": error[:500]},
                        level="WARNING",
                        status_message=error[:500],
                    )
                    return "", [], error
                raise

            context_message = build_rag_context_message(hits)
            sources = [serialize_rag_hit(hit, rank=index) for index, hit in enumerate(hits, start=1)]
            observation.update(
                output={
                    "hit_count": len(hits),
                    "context_chars": len(context_message),
                    "source_count": len(sources),
                    "sources": [
                        {
                            "rank": source["rank"],
                            "document_id": source["document_id"],
                            "chunk_id": source["chunk_id"],
                            "chunk_index": source["chunk_index"],
                            "score": source["score"],
                            "rerank_score": source["rerank_score"],
                        }
                        for source in sources
                    ],
                }
            )
            return context_message, sources, ""
