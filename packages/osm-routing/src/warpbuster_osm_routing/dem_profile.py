"""Versioned Mapzen/Tilezen Skadi data contract; no acquisition or sampling."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

DATASET_PROFILE_SCHEMA_VERSION = 1
ATTRIBUTION_BUNDLE_VERSION = 1

FORMATS_URL = "https://github.com/tilezen/joerd/blob/master/docs/formats.md"
SOURCES_URL = "https://github.com/tilezen/joerd/blob/master/docs/data-sources.md"
ATTRIBUTION_URL = "https://github.com/tilezen/joerd/blob/master/docs/attribution.md"
AWS_DATASET_URL = "https://registry.opendata.aws/terrain-tiles/"


@dataclass(frozen=True, slots=True)
class DemAttribution:
    """One source credit; exact obligations remain governed by the provider terms."""

    source: str
    credit: str

    def as_dict(self) -> dict[str, str]:
        return {"source": self.source, "credit": self.credit}


# The provider's published attribution document is the authority. This conservative
# bundle includes every listed source, since a Skadi HGT tile does not identify its
# contributing inputs and the composited dataset is not covered by the Joerd MIT license.
SKADI_ATTRIBUTIONS: tuple[DemAttribution, ...] = (
    DemAttribution("Mapzen/Tilezen", "Terrain tile processing by Mapzen/Tilezen"),
    DemAttribution(
        "3DEP / GMTED2010 / SRTM", "Terrain data courtesy of the U.S. Geological Survey"
    ),
    DemAttribution(
        "ArcticDEM",
        "DEM created from DigitalGlobe imagery; funded by NSF awards 1043681, 1559691 and 1542736",
    ),
    DemAttribution("Australia", "© Commonwealth of Australia (Geoscience Australia) 2017"),
    DemAttribution(
        "Austria", "© offene Daten Österreichs - Digitales Geländemodell (DGM) Österreich"
    ),
    DemAttribution(
        "Canada", "Contains information licensed under the Open Government Licence - Canada"
    ),
    DemAttribution(
        "ETOPO1", "Global terrain data: U.S. National Oceanic and Atmospheric Administration"
    ),
    DemAttribution(
        "EU-DEM",
        "Produced using Copernicus data and information funded by the European Union - EU-DEM layers",
    ),
    DemAttribution("INEGI", "Source: INEGI, Continental relief, 2016"),
    DemAttribution(
        "LINZ",
        "Copyright 2011 Crown copyright © Land Information New Zealand and the New Zealand Government",
    ),
    DemAttribution("Norway", "© Kartverket"),
    DemAttribution("United Kingdom", "© Environment Agency copyright and/or database right 2015"),
)


@dataclass(frozen=True, slots=True)
class SkadiDatasetProfile:
    """Immutable source/format identity for Task 020A and later DEM snapshots."""

    profile_id: str = "mapzen-skadi-egm96-v1"

    def canonical_document(self) -> dict[str, Any]:
        """Return detached semantic data; callers may not mutate the profile."""
        return {
            "schema_version": DATASET_PROFILE_SCHEMA_VERSION,
            "profile_id": self.profile_id,
            "provider": "Mapzen/Tilezen Terrain Tiles",
            "dataset": "Skadi",
            "source_origin": "https://elevation-tiles-prod.s3.us-east-1.amazonaws.com",
            "source_path_template": "/skadi/{latitude_directory}/{tile_name}.hgt.gz",
            "horizontal_crs": "EPSG:4326",
            "vertical_datum": "WGS84/EGM96 geoid",
            "elevation_unit": "metre",
            "tile_extent_degrees": 1,
            "grid_samples_per_side": 3601,
            "nominal_grid_spacing_arcseconds": 1,
            "uncompressed_tile_bytes": 3601 * 3601 * 2,
            "sample_encoding": "signed-int16-big-endian",
            "compression": "gzip",
            "void_value": -32768,
            "includes_bathymetry": True,
            "attribution_bundle_version": ATTRIBUTION_BUNDLE_VERSION,
            "attributions": [item.as_dict() for item in SKADI_ATTRIBUTIONS],
        }

    def sha256(self) -> str:
        encoded = json.dumps(
            self.canonical_document(), sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def inspection_document(self) -> dict[str, Any]:
        document = self.canonical_document()
        document["profile_sha256"] = self.sha256()
        document["documentation"] = {
            "dataset": AWS_DATASET_URL,
            "format": FORMATS_URL,
            "source_resolution": SOURCES_URL,
            "attribution_and_terms": ATTRIBUTION_URL,
        }
        document["limitations"] = [
            "One-arcsecond output grid is not a guarantee of one-arcsecond source resolution or accuracy.",
            "Input sources vary by region; land elevation and ocean bathymetry are composited.",
            "Negative elevations are valid; void is -32768, not zero.",
            "The Joerd software MIT license is not a license for the composite terrain dataset.",
            "Attribution obligations vary by input provider; consult the linked provider document.",
        ]
        return document


MAPZEN_SKADI_EGM96_V1 = SkadiDatasetProfile()
