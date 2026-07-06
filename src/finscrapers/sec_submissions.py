"""SEC EDGAR submissions adapter — entity classification (SIC) facts.

The submissions endpoint (data.sec.gov, public domain) carries per-company
registration metadata alongside the filing index; FinField uses it for the
SIC industry code that feeds the Sector/Industry lenses. SIC is slowly
changing, so the fact is keyed by observation date (``period.end`` = the day
we read it), per the field convention for slowly-changing attributes.

``sicDescription`` is text and FinFact values are integers only, so it is
never emitted; the lens maps code -> label itself.

Rate limits: same etiquette as companyfacts (<=10 req/s, descriptive
User-Agent); identity resolution (ticker map, CIK) is inherited from
:class:`~finscrapers.sec_edgar.SecEdgarSource`.
"""
from __future__ import annotations

import json
import time
from datetime import date
from decimal import Decimal
from typing import Optional

from finfacts.model import Entity, FactSet, FinFact, Period, Source

from .http import get as _get
from .sec_edgar import SecEdgarSource

SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"


class SecSubmissionsSource(SecEdgarSource):
    """Company classification facts from the SEC submissions JSON."""

    kind = "sec-submissions"

    def fetch(self, entity: Entity) -> Optional[FactSet]:
        cik = self.resolve_cik(entity)
        if cik is None:
            return None
        url = SUBMISSIONS_URL.format(cik=cik)
        cache = self.cache_dir / f"submissions-CIK{cik:010d}.json" if self.cache_dir else None
        if cache and cache.exists():
            raw = cache.read_bytes()
        else:
            time.sleep(self.throttle)
            try:
                raw = _get(url)
            except Exception:
                return None
            if cache:
                cache.parent.mkdir(parents=True, exist_ok=True)
                cache.write_bytes(raw)
        return self.normalize(entity, json.loads(raw, parse_float=Decimal), url)

    def normalize(self, entity: Entity, doc: dict, ref: str) -> Optional[FactSet]:
        """Turn one submissions document into a FactSet (SIC only, for now)."""
        fs = FactSet(entity=entity)
        today = date.today().isoformat()
        sic = str(doc.get("sic") or "").strip()
        # skip silently-invalid codes: "", "0000", non-digit
        if sic.isdigit() and int(sic) != 0:
            fs.add(FinFact(
                entity_id=entity.entity_id,
                concept="finfield:sic",
                value=int(sic),
                scale=0,
                unit="pure",
                period=Period(end=today),  # date-keyed by observation date
                source=Source(kind=self.kind, ref=ref, fetched=today),
            ))
        return fs.dedupe() if fs.facts else None
