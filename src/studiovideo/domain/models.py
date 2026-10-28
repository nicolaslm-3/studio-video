"""Entités métier (objets simples, sans dépendance à l'interface)."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Platform(str, Enum):
    YOUTUBE = "youtube"
    TIKTOK = "tiktok"
    INSTAGRAM = "instagram"
    FACEBOOK = "facebook"
    X = "x"

    @property
    def label(self) -> str:
        return {
            "youtube": "YouTube", "tiktok": "TikTok", "instagram": "Instagram",
            "facebook": "Facebook", "x": "X / Twitter",
        }[self.value]


class PublicationStatus(str, Enum):
    DRAFT = "draft"
    READY = "ready"
    SCHEDULED_LOCAL = "scheduled_local"
    SCHEDULED_PLATFORM = "scheduled_platform"
    UPLOADING = "uploading"
    PROCESSING_REMOTE = "processing_remote"
    PUBLISHED = "published"
    FAILED = "failed"
    MANUAL_ACTION = "manual_action"
    CANCELLED = "cancelled"

    @property
    def label(self) -> str:
        return {
            "draft": "Brouillon", "ready": "Prête",
            "scheduled_local": "Programmée localement",
            "scheduled_platform": "Programmée sur la plateforme",
            "uploading": "En cours de transfert",
            "processing_remote": "En traitement distant",
            "published": "Publiée", "failed": "Échec",
            "manual_action": "Action manuelle requise", "cancelled": "Annulée",
        }[self.value]


class ProjectMode(str, Enum):
    SMART_CLIPPING = "smart_clipping"
    TRANSLATION = "translation"
    ORIGINAL = "original"
    FREE_EDIT = "free_edit"
    TEXTS = "texts"

    @property
    def label(self) -> str:
        return {
            "smart_clipping": "Clipping",
            "translation": "Traduction",
            "original": "Création originale",
            "free_edit": "Montage libre",
            "texts": "Textes",
        }[self.value]


@dataclass
class Concept:
    id: int
    workspace_id: int
    name: str
    theme: str = ""
    color: str = "#8A8F98"
    language: str = "fr"
    archived: bool = False


@dataclass
class SocialAccount:
    id: int
    workspace_id: int
    platform: Platform
    name: str
    public_url: str = ""
    language: str = "fr"
    connection_status: str = "disconnected"
    avatar_path: str = ""                    # photo de profil enregistrée à la connexion (si le réseau la donne)

    @property
    def display(self) -> str:
        return f"{self.platform.label} — {self.name}"


@dataclass
class Destination:
    """Compte proposé comme destination pour une vidéo d'un concept."""
    account: SocialAccount
    in_concept: bool
    default_selected: bool


@dataclass
class Publication:
    id: int
    master_id: int
    account: SocialAccount
    is_exceptional: bool
    title: str
    description: str
    hashtags: str
    scheduled_at: str | None
    schedule_mode: str
    status: PublicationStatus
    overrides: set[str] = field(default_factory=set)
