from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from backend.api.dependencies import get_services
from backend.services import ApplicationServices
from backend.reading_scorer import ReadingScoreJobs

router = APIRouter(prefix="/library", tags=["local-reading-scores"])


class ScoreRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    document_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    page: int = Field(ge=1, strict=True)
    text: str = Field(min_length=1, max_length=100_000)
    text_parser_version: str = Field(pattern=r"^pdfjs-text-items-v1/5\.[0-9.]+/items-newline-v1$")
    context_tokens: int = Field(default=256, ge=2, le=1024, strict=True)
    stride: int = Field(default=128, ge=1, le=1023, strict=True)

    @field_validator("text")
    @classmethod
    def valid_unicode(cls, value):
        value.encode("utf-8")
        return value

    @model_validator(mode="after")
    def context_overlap(self):
        if self.stride >= self.context_tokens:
            raise ValueError("Stride must be smaller than context length")
        return self


def service(services):
    if services.reading_scores is None:
        services.reading_scores = ReadingScoreJobs(services)
    return services.reading_scores


@router.get("/reading-scorer")
async def status(services: ApplicationServices = Depends(get_services)):
    return service(services).scorer.status()


@router.post("/papers/{node_id}/reading-scores", status_code=202)
async def score(node_id: str, request: ScoreRequest, services: ApplicationServices = Depends(get_services)):
    return await service(services).create(node_id, request)


@router.get("/papers/{node_id}/reading-scores/{job_id}")
async def result(node_id: str, job_id: str, services: ApplicationServices = Depends(get_services)):
    return service(services).read(node_id, job_id)


@router.delete("/papers/{node_id}/reading-scores/{job_id}")
async def cancel(node_id: str, job_id: str, services: ApplicationServices = Depends(get_services)):
    return await service(services).cancel(node_id, job_id)
