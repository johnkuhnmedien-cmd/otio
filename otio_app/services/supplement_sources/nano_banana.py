"""Nano Banana / Gemini Image — KI-Bildgenerierung."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path

from otio_app.analysis_models import SupplementAssetSidecar, SupplementCandidate, SupplementRequest
from otio_app.defaults import (
    CANDIDATE_STATUS_MOCK_ONLY,
    PROVIDER_STATUS_MOCK,
    RIGHTS_STATUS_GENERATED_APPROVED,
    SUPPLEMENT_SOURCE_NANO_BANANA,
)
from otio_app.services.supplement_sources.base import ProviderReadiness, SupplementAsset, SupplementSourceAdapter


class NanoBananaAdapter(SupplementSourceAdapter):
    provider = SUPPLEMENT_SOURCE_NANO_BANANA
    model = "gemini-2.5-flash-image"
    prompt_version = "supplement_v1"

    def readiness(self) -> ProviderReadiness:
        return ProviderReadiness(
            provider=self.provider,
            status=PROVIDER_STATUS_MOCK,
            message="Gemini Image / Nano Banana ist noch nicht echt angebunden.",
            search_enabled=True,
            generate_enabled=False,
            is_mock=True,
        )

    def search(self, request: SupplementRequest) -> list[SupplementCandidate]:
        prompt = request.generation_prompt or request.visual_requirement or request.passage_text
        return [
            SupplementCandidate(
                candidate_id=f"cand_{uuid.uuid4().hex[:8]}",
                supplement_request_id=request.supplement_request_id,
                provider=self.provider,
                provider_asset_id=f"nano_{uuid.uuid4().hex[:6]}",
                title=f"KI-Bild: {prompt[:40]}",
                description=prompt,
                media_type="image",
                width=3840,
                height=2160,
                requires_purchase=False,
                requires_user_approval=True,
                match_score=0.8,
                match_reason="KI-Generierung geplant",
                status=CANDIDATE_STATUS_MOCK_ONLY,
                provider_status=PROVIDER_STATUS_MOCK,
                is_mock=True,
                download_enabled=False,
            )
        ]

    def generate(
        self,
        request: SupplementRequest,
        destination_folder: Path,
    ) -> SupplementAsset:
        raise PermissionError("Nano Banana/Gemini Image ist noch nicht produktiv angebunden.")
        destination_folder.mkdir(parents=True, exist_ok=True)
        prompt = request.generation_prompt or request.visual_requirement or request.passage_text
        filename = f"{request.supplement_request_id}_generated.png"
        local_path = destination_folder / filename
        try:
            from PIL import Image, ImageDraw

            image = Image.new("RGB", (1920, 1080), color=(32, 48, 72))
            draw = ImageDraw.Draw(image)
            draw.text((40, 500), prompt[:120], fill=(255, 255, 255))
            image.save(local_path)
        except ImportError:
            local_path.write_bytes(b"png-placeholder")
        sidecar = SupplementAssetSidecar(
            asset_id=f"asset_nano_{request.supplement_request_id}",
            supplement_request_id=request.supplement_request_id,
            provider=self.provider,
            provider_asset_id=request.supplement_request_id,
            prompt=prompt,
            acquisition_method="generate",
            generated_at=datetime.now(timezone.utc),
            original_filename=filename,
            local_path=str(local_path),
            rights_status=RIGHTS_STATUS_GENERATED_APPROVED,
            model=self.model,
            generation_aspect_ratio="16:9",
            output_resolution="1920x1080",
            generation_settings={"prompt_version": self.prompt_version},
            synthid_expected=True,
            approval_status="GENERATED",
        )
        return SupplementAsset(local_path=local_path, sidecar=sidecar)
