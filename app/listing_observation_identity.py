from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Iterable, Mapping

from app.enums import ProductMappingStatus


@dataclass(frozen=True, slots=True)
class ListingObservationSourceIdentity:
    """Frozen Task 13 mapping identity for one snapshot item and page."""

    snapshot_item_id: str
    page: str
    page_identity_key: str
    evidence_sha256: str
    mapping_status: ProductMappingStatus
    internal_sku: str | None
    candidate_internal_skus: tuple[str, ...]


def listing_observation_source_identities(
    *,
    snapshot_id: str,
    evidence_manifest_sha256: str,
    snapshot_items: Iterable[Mapping[str, object]],
) -> tuple[ListingObservationSourceIdentity, ...]:
    identities: list[ListingObservationSourceIdentity] = []
    for item in snapshot_items:
        internal_sku = str(item.get("internal_sku") or "").strip() or None
        affected = item.get("affected_internal_skus")
        if not isinstance(affected, (list, tuple)):
            raise ValueError("affected_internal_skus must be a list")
        affected_skus = tuple(
            sorted(
                {
                    str(candidate or "").strip()
                    for candidate in affected
                    if str(candidate or "").strip()
                }
            )
        )
        if internal_sku is not None:
            mapping_status = ProductMappingStatus.VERIFIED
            candidate_internal_skus = (internal_sku,)
        elif affected_skus:
            mapping_status = ProductMappingStatus.AMBIGUOUS
            candidate_internal_skus = affected_skus
        else:
            mapping_status = ProductMappingStatus.UNMAPPED
            candidate_internal_skus = ()

        for page in ("online", "waiting"):
            if int(item[f"{page}_occurrences"]) == 0:
                continue
            evidence_payload = {
                "snapshot_id": snapshot_id,
                "snapshot_item_id": item["snapshot_item_id"],
                "page": page,
                "row_identities": item[f"{page}_row_identities"],
                "evidence_manifest_sha256": evidence_manifest_sha256,
            }
            evidence_sha256 = "sha256:" + hashlib.sha256(
                json.dumps(
                    evidence_payload,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
            identities.append(
                ListingObservationSourceIdentity(
                    snapshot_item_id=str(item["snapshot_item_id"]),
                    page=page,
                    page_identity_key=str(item["page_identity_key"]),
                    evidence_sha256=evidence_sha256,
                    mapping_status=mapping_status,
                    internal_sku=internal_sku,
                    candidate_internal_skus=candidate_internal_skus,
                )
            )
    identities.sort(key=lambda identity: identity.evidence_sha256)
    if len({identity.evidence_sha256 for identity in identities}) != len(
        identities
    ):
        raise ValueError("snapshot observation evidence identities must be unique")
    return tuple(identities)


def listing_observation_source_identity_payload(
    identities: Iterable[ListingObservationSourceIdentity],
) -> list[dict[str, object]]:
    payload = [
        {
            "snapshot_item_id": identity.snapshot_item_id,
            "page": identity.page,
            "page_identity_key": identity.page_identity_key,
            "evidence_sha256": identity.evidence_sha256,
            "mapping_status": identity.mapping_status.value,
            "internal_sku": identity.internal_sku,
            "candidate_internal_skus": list(
                identity.candidate_internal_skus
            ),
        }
        for identity in identities
    ]
    payload.sort(
        key=lambda item: json.dumps(
            item,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return payload


def listing_observation_source_identity_sha256(
    identities: Iterable[ListingObservationSourceIdentity],
) -> str:
    encoded = json.dumps(
        listing_observation_source_identity_payload(identities),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()
