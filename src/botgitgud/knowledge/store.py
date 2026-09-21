"""Local-only consumption boundary and validated atomic artifact persistence."""

import os
from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile

from botgitgud.domain.specs import SpecId
from botgitgud.knowledge.rotation_knowledge import KnowledgeState, RotationKnowledge
from botgitgud.knowledge.spec_slugs import SPEC_SLUGS, spec_key


@dataclass(frozen=True)
class KnowledgeSnapshot:
    state: KnowledgeState
    artifact: RotationKnowledge | None


class KnowledgeStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self._verified: dict[tuple[str, str], str] = {}

    def path_for(self, spec: SpecId) -> Path:
        slug = SPEC_SLUGS[spec_key(spec)]
        return self.root / f"{slug.class_slug}-{slug.spec_slug}.json"

    def read(self, spec: SpecId) -> KnowledgeSnapshot:
        """Absent/corrupt knowledge degrades locally; never checks the website."""
        try:
            artifact = RotationKnowledge.model_validate_json(self.path_for(spec).read_bytes())
            if (artifact.class_name, artifact.spec_name) != spec_key(spec):
                raise ValueError("Artifact belongs to another spec")
        except (OSError, ValueError, KeyError):
            return KnowledgeSnapshot(KnowledgeState.UNAVAILABLE, None)
        state = KnowledgeState.STALE_UNVERIFIED
        if self._verified.get(spec_key(spec)) == artifact.source_fingerprint:
            state = KnowledgeState.CURRENT
        return KnowledgeSnapshot(state, artifact)

    def mark_unverified(self, spec: SpecId) -> None:
        self._verified.pop(spec_key(spec), None)

    def mark_verified(self, artifact: RotationKnowledge) -> None:
        self._verified[(artifact.class_name, artifact.spec_name)] = artifact.source_fingerprint

    def write(self, artifact: RotationKnowledge) -> None:
        # Revalidate even model_construct/model_copy input before creating a temp file.
        payload = artifact.model_dump_json(indent=2)
        validated = RotationKnowledge.model_validate_json(payload)
        destination = self.path_for(SpecId(validated.class_name, validated.spec_name))
        self.root.mkdir(parents=True, exist_ok=True)
        temporary: Path | None = None
        try:
            with NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.root, delete=False) as f:
                temporary = Path(f.name)
                f.write(payload)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temporary, destination)  # noqa: PTH105 — explicit atomic replace contract
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        self.mark_verified(validated)
