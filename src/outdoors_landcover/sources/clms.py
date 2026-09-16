"""Copernicus Land Monitoring Service High Resolution Layer access via CDSE."""

from __future__ import annotations

import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from outdoors_landcover.exceptions import ExtractUnavailableError
from outdoors_landcover.extract.raster import polygonize_classified_raster
from outdoors_landcover.logging import get_logger
from outdoors_landcover.mapping.clms import ClmsConfig, ClmsLayerConfig
from outdoors_landcover.sources.cache import download_file, ensure_free_space

logger = get_logger(__name__)

_RASTER_SUFFIXES = {".tif", ".tiff", ".cog"}


@dataclass(frozen=True)
class ClmsProduct:
    layer: str
    product_id: str
    name: str
    url: str
    local_path: Path | None = None


def _polygon_wkt(bbox: tuple[float, float, float, float]) -> str:
    w, s, e, n = bbox
    return f"POLYGON(({w} {s},{e} {s},{e} {n},{w} {n},{w} {s}))"


def _year_window(year: int) -> tuple[str, str]:
    start = f"{year}-01-01T00:00:00.000Z"
    end = f"{year + 1}-01-01T00:00:00.000Z"
    return start, end


class ClmsHrlProvider:
    """
    Fetch pan-European CLMS High Resolution Layers (forest type, grassland, crop types).

    Catalogue search is unauthenticated. Product download uses a CDSE token when
    OUTDOORS_CDSE_USERNAME / OUTDOORS_CDSE_PASSWORD are set. Local GeoTIFFs under
    clms_local_root skip the network entirely.
    """

    def __init__(
        self,
        config: ClmsConfig,
        *,
        cache_dir: Path,
        local_root: Path | None = None,
        offline: bool = False,
        username: str | None = None,
        password: str | None = None,
        min_free_bytes: int = 1_000_000_000,
        concurrency: int = 4,
        year: int | None = None,
    ) -> None:
        self.config = config
        self.cache_dir = cache_dir / "clms"
        self.local_root = local_root
        self.offline = offline
        self.username = username
        self.password = password
        self.min_free_bytes = min_free_bytes
        self.concurrency = max(1, concurrency)
        self.year = year or config.year
        self._token: str | None = None

    def plan(
        self,
        bbox: tuple[float, float, float, float],
    ) -> list[dict[str, Any]]:
        planned: list[dict[str, Any]] = []
        for name, layer in self.config.layers.items():
            local_files = self._local_rasters(layer)
            planned.append(
                {
                    "layer": name,
                    "outdoors_class": layer.outdoors_class,
                    "dataset_identifier": layer.dataset_identifier,
                    "year": self.year,
                    "native_resolution_m": self.config.native_resolution_m,
                    "local_files": [str(p) for p in local_files],
                    "offline": self.offline,
                    "bbox": list(bbox),
                }
            )
        return planned

    def ingest(
        self,
        bbox: tuple[float, float, float, float],
    ) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
        """Download (in parallel) and polygonize all configured HRL layers."""
        buckets: dict[str, list[dict[str, Any]]] = {
            layer.outdoors_class: [] for layer in self.config.layers.values()
        }
        meta: dict[str, Any] = {
            "year": self.year,
            "native_resolution_m": self.config.native_resolution_m,
            "layers": {},
        }

        def process_layer(
            layer: ClmsLayerConfig,
        ) -> tuple[str, list[dict[str, Any]], dict[str, Any]]:
            rasters = self._ensure_rasters(layer, bbox)
            features: list[dict[str, Any]] = []
            for raster in rasters:
                features.extend(
                    polygonize_classified_raster(
                        raster,
                        layer,
                        bbox_wgs84=bbox,
                        polygonize_resolution_m=self.config.polygonize_resolution_m,
                        native_resolution_m=self.config.native_resolution_m,
                    )
                )
            stats = {"files": [str(p) for p in rasters], "features": len(features)}
            return layer.outdoors_class, features, stats

        layers = list(self.config.layers.values())
        if self.concurrency == 1 or len(layers) <= 1:
            results = [process_layer(layer) for layer in layers]
        else:
            results = []
            with ThreadPoolExecutor(max_workers=min(self.concurrency, len(layers))) as pool:
                futs = {pool.submit(process_layer, layer): layer for layer in layers}
                for fut in as_completed(futs):
                    results.append(fut.result())

        for outdoors_class, features, stats in results:
            buckets.setdefault(outdoors_class, []).extend(features)
            meta["layers"][outdoors_class] = stats
        return buckets, meta

    def _local_rasters(self, layer: ClmsLayerConfig) -> list[Path]:
        if self.local_root is None or not self.local_root.is_dir():
            return []
        matches: list[Path] = []
        specific = self.local_root / layer.name
        search_roots = [specific] if specific.is_dir() else [self.local_root]
        for root in search_roots:
            for pattern in ("*.tif", "*.tiff", "*.cog", "*.TIF"):
                matches.extend(sorted(root.glob(pattern)))
        # Prefer files whose name mentions the layer when mixed in one folder.
        named = [
            p
            for p in matches
            if layer.name in p.name.lower() or layer.outdoors_class in p.name.lower()
        ]
        return named or matches

    def _ensure_rasters(
        self, layer: ClmsLayerConfig, bbox: tuple[float, float, float, float]
    ) -> list[Path]:
        local = self._local_rasters(layer)
        if local:
            logger.info("CLMS %s: using %s local raster(s)", layer.name, len(local))
            return local

        cache_hits = list((self.cache_dir / layer.dataset_identifier).glob("*"))
        rasters = [p for p in cache_hits if p.suffix.lower() in _RASTER_SUFFIXES]
        if rasters:
            logger.info("CLMS %s: using %s cached raster(s)", layer.name, len(rasters))
            return rasters

        if self.offline:
            raise ExtractUnavailableError(
                f"CLMS {layer.name} not in local root/cache and offline mode is on"
            )

        products = self._search_products(layer, bbox)
        if not products:
            raise ExtractUnavailableError(
                f"No CLMS products for {layer.dataset_identifier} year={self.year} bbox={bbox}"
            )
        ensure_free_space(self.cache_dir, self.min_free_bytes)
        downloaded: list[Path] = []
        with ThreadPoolExecutor(max_workers=self.concurrency) as pool:
            futs = {pool.submit(self._download_product, product): product for product in products}
            for fut in as_completed(futs):
                downloaded.extend(self._as_rasters(fut.result()))
        if not downloaded:
            raise ExtractUnavailableError(f"CLMS download produced no rasters for {layer.name}")
        return downloaded

    def _search_products(
        self, layer: ClmsLayerConfig, bbox: tuple[float, float, float, float]
    ) -> list[ClmsProduct]:
        start, end = _year_window(self.year)
        geom = _polygon_wkt(bbox)
        filt = (
            "Collection/Name eq 'CLMS' and "
            "Attributes/OData.CSC.StringAttribute/any("
            f"att:att/Name eq 'datasetIdentifier' and "
            f"att/OData.CSC.StringAttribute/Value eq '{layer.dataset_identifier}'"
            ") and "
            "Attributes/OData.CSC.StringAttribute/any("
            "att:att/Name eq 'fileFormat' and att/OData.CSC.StringAttribute/Value eq 'cog'"
            ") and "
            f"ContentDate/Start ge {start} and ContentDate/Start lt {end} and "
            f"OData.CSC.Intersects(area=geography'SRID=4326;{geom}')"
        )
        url = f"{self.config.catalogue_url}/Products"
        products: list[ClmsProduct] = []
        skip = 0
        top = 100
        with httpx.Client(timeout=60.0, follow_redirects=True) as client:
            while True:
                resp = client.get(
                    url,
                    params={"$filter": filt, "$top": top, "$skip": skip, "$select": "Id,Name"},
                )
                if resp.status_code >= 400:
                    raise ExtractUnavailableError(
                        f"CLMS catalogue search failed for {layer.dataset_identifier}: "
                        f"HTTP {resp.status_code}"
                    )
                payload = resp.json()
                items = payload.get("value") or []
                for item in items:
                    pid = str(item.get("Id") or "")
                    name = str(item.get("Name") or pid)
                    if not pid:
                        continue
                    products.append(
                        ClmsProduct(
                            layer=layer.name,
                            product_id=pid,
                            name=name,
                            url=f"{self.config.download_url}/Products({pid})/$value",
                        )
                    )
                if len(items) < top:
                    break
                skip += top
        logger.info(
            "CLMS catalogue %s year=%s: %s product(s)",
            layer.dataset_identifier,
            self.year,
            len(products),
        )
        return products

    def _download_product(self, product: ClmsProduct) -> Path:
        dest_dir = self.cache_dir / product.layer
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / f"{product.product_id}.bin"
        if dest.is_file() and dest.stat().st_size > 0:
            return dest
        headers = {}
        token = self._access_token()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return download_file(product.url, dest, timeout=300.0, headers=headers or None)

    def _as_rasters(self, path: Path) -> list[Path]:
        if path.suffix.lower() in _RASTER_SUFFIXES:
            return [path]
        if zipfile.is_zipfile(path):
            out_dir = path.with_suffix("")
            out_dir.mkdir(parents=True, exist_ok=True)
            rasters: list[Path] = []
            with zipfile.ZipFile(path) as zf:
                for info in zf.infolist():
                    suffix = Path(info.filename).suffix.lower()
                    if suffix not in _RASTER_SUFFIXES:
                        continue
                    target = out_dir / Path(info.filename).name
                    if not target.is_file():
                        zf.extract(info, out_dir)
                        extracted = out_dir / info.filename
                        if extracted != target and extracted.is_file():
                            extracted.replace(target)
                    rasters.append(target)
            return rasters
        # CDSE COG often arrives without a suffix; sniff TIFF magic.
        with path.open("rb") as fh:
            header = fh.read(4)
        if header in {b"II*\x00", b"MM\x00*"} or header[:2] in {b"II", b"MM"}:
            tif = path.with_suffix(".tif")
            if not tif.exists():
                path.replace(tif)
                return [tif]
            return [tif]
        logger.warning("CLMS product is not a raster or zip: %s", path)
        return []

    def _access_token(self) -> str | None:
        if self._token:
            return self._token
        if not self.username or not self.password:
            logger.info("No CDSE credentials; catalogue-only / cached CLMS access")
            return None
        resp = httpx.post(
            self.config.token_url,
            data={
                "client_id": "cdse-public",
                "username": self.username,
                "password": self.password,
                "grant_type": "password",
            },
            timeout=30.0,
        )
        if resp.status_code >= 400:
            raise ExtractUnavailableError(f"CDSE token request failed: HTTP {resp.status_code}")
        token = resp.json().get("access_token")
        if not token:
            raise ExtractUnavailableError("CDSE token response missing access_token")
        self._token = str(token)
        return self._token


def clms_plan_record(
    config: ClmsConfig,
    *,
    bbox: tuple[float, float, float, float] | None,
    year: int,
    used: bool,
    reason: str,
) -> dict[str, Any]:
    return {
        "service": "Copernicus Land Monitoring Service",
        "product_family": "High Resolution Layer (Vegetated Land Cover Characteristics)",
        "not": "CORINE Land Cover (CLC)",
        "used": used,
        "reason": reason,
        "year": year,
        "native_resolution_m": config.native_resolution_m,
        "bbox": list(bbox) if bbox else None,
        "datasets": {
            name: layer.dataset_identifier for name, layer in config.layers.items()
        },
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
