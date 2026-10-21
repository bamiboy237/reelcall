import time
from pathlib import Path
from statistics import mean
from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, Field, model_validator

from video_pipeline.embeddings import Embedder
from video_pipeline.search import SearchHit, SearchRequest, SearchStore
from video_pipeline.storage import VideoRecord


class ReferenceMoment(BaseModel):
    query: str
    modality: Literal["speech", "visual", "sound"]
    start_seconds: Annotated[float, Field(ge=0, allow_inf_nan=False)]
    end_seconds: Annotated[float, Field(gt=0, allow_inf_nan=False)]

    @model_validator(mode="after")
    def validate_range(self) -> Self:
        if self.end_seconds <= self.start_seconds:
            raise ValueError("Reference end must be after its start.")
        return self


class ReferenceVideo(BaseModel):
    video_filename: str
    items: list[ReferenceMoment] = Field(min_length=1)


def interval_iou(reference: ReferenceMoment, hit: SearchHit) -> float:
    overlap = max(
        0,
        min(reference.end_seconds, hit.end_seconds)
        - max(reference.start_seconds, hit.start_seconds),
    )
    union = max(reference.end_seconds, hit.end_seconds) - min(
        reference.start_seconds, hit.start_seconds
    )
    return overlap / union


def evaluate_search(
    references: list[Path], store: SearchStore, embedder: Embedder
) -> dict[str, Any]:
    rows = store.load_windows()
    videos = {
        row["video_id"]: VideoRecord.model_validate_json(row["video_record"])
        for row in rows
    }
    cases = []
    for path in references:
        reference = ReferenceVideo.model_validate_json(path.read_text(encoding="utf-8"))
        matches = [
            video
            for video in videos.values()
            if video.filename == reference.video_filename
        ]
        if len(matches) != 1:
            raise ValueError(
                f"{reference.video_filename}: expected one indexed video, "
                f"found {len(matches)}."
            )
        video = matches[0]
        for item in reference.items:
            if (
                video.duration_seconds is None
                or item.end_seconds > video.duration_seconds
            ):
                raise ValueError(f"{path}: reference interval exceeds video duration.")
            item.query = SearchRequest(query=item.query).query
            cases.append((video, item))
    if not cases:
        raise ValueError("Provide at least one reference JSON file.")

    started = time.perf_counter()
    vectors = embedder.queries([item.query for _, item in cases])
    encoding_seconds = time.perf_counter() - started
    results: list[dict[str, Any]] = []
    for (video, item), vector in zip(cases, vectors, strict=True):
        for mode in ("visual", "audio", "combined", "all"):
            started = time.perf_counter()
            response = store.rank(
                SearchRequest(query=item.query, mode=mode), vector, rows
            )
            ious = [
                interval_iou(item, hit) if hit.video_id == video.id else 0.0
                for hit in response.matches
            ]
            first = response.matches[0] if response.matches else None
            results.append(
                {
                    "query": item.query,
                    "modality": item.modality,
                    "mode": mode,
                    "expected_video_id": str(video.id),
                    "expected_filename": video.filename,
                    "expected_start_seconds": item.start_seconds,
                    "expected_end_seconds": item.end_seconds,
                    "video_at_1": first is not None and first.video_id == video.id,
                    "overlap_at_1": bool(ious and ious[0] > 0),
                    "overlap_at_5": any(iou > 0 for iou in ious),
                    "iou_at_1": ious[0] if ious else 0.0,
                    "best_iou_at_5": max(ious, default=0.0),
                    "ranking_seconds": time.perf_counter() - started,
                    "matches": [
                        hit.model_dump(mode="json") for hit in response.matches
                    ],
                }
            )
    summary = {}
    for mode in ("visual", "audio", "combined", "all"):
        summary[mode] = {}
        for modality in ("total", "speech", "visual", "sound"):
            group = [
                result
                for result in results
                if result["mode"] == mode
                and (modality == "total" or result["modality"] == modality)
            ]
            if not group:
                continue
            summary[mode][modality] = {
                "queries": len(group),
                **{
                    key: round(mean(result[key] for result in group), 4)
                    for key in (
                        "video_at_1",
                        "overlap_at_1",
                        "overlap_at_5",
                        "iou_at_1",
                        "best_iou_at_5",
                        "ranking_seconds",
                    )
                },
                "iou_0_5_at_1": round(
                    mean(result["iou_at_1"] >= 0.5 for result in group), 4
                ),
            }
    return {
        "query_encoding_seconds": round(encoding_seconds, 3),
        "runtime": embedder.runtime(),
        "indexes": [record.model_dump(mode="json") for record in store.indexes()],
        "summary": summary,
        "results": results,
    }
