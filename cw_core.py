"""cw_core.py - pure decision logic for the HSR Currency Wars assistant.

This module is deliberately free of cv2 / win32 / Tk imports. Everything here
can run (and be unit tested) without a game, a screen or a GUI. The application
script imports these functions and only performs the side effects (capture,
clicks, drawing) itself.

Design rule: functions in this file never click, never sleep, never read the
screen. They take data in and return decisions out.
"""
from __future__ import annotations

import difflib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

_WORD_SPLIT = re.compile(r"[^a-z0-9]+")
_NON_ALNUM = re.compile(r"[^a-z0-9]+")

KeyFn = Callable[[str], str]


# ---------------------------------------------------------------------------
# Naming helpers
# ---------------------------------------------------------------------------

def slugify(name: str) -> str:
    """Return the lower_snake slug used for template file names."""
    return _WORD_SPLIT.sub("_", str(name).strip().lower()).strip("_")


def match_key(name: str) -> str:
    """Return an aggressive comparison key: lowercase, alphanumerics only.

    ``match_key("Topaz and Numby") == match_key("TopazandNumby") == "topazandnumby"``
    """
    return _NON_ALNUM.sub("", str(name).lower())


def coerce_number(raw: Any, fallback: Any = None) -> Any:
    """Best-effort conversion of a UI/config string to int or float.

    Empty strings and unparsable values fall back to the original value (or the
    provided fallback) instead of raising.
    """
    if isinstance(raw, bool) or isinstance(raw, (int, float)):
        return raw
    if raw is None:
        return fallback if fallback is not None else raw
    text = str(raw).strip()
    if not text:
        return fallback if fallback is not None else raw
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        return fallback if fallback is not None else raw


# ---------------------------------------------------------------------------
# Name registry: one source of truth for names, aliases and template files
# ---------------------------------------------------------------------------

class NameRegistry:
    """Resolve the many spellings of a name onto one canonical template stem.

    Three name spaces exist in this project and they do not always agree:

    * template files (``templates/units/TopazandNumby.png``)
    * the strategy JSON (``"Topaz"``, ``"March 7th"``)
    * OCR output (``"T0PAZ AND NUMBY"``)

    The registry maps all of them onto the template stem (``TopazandNumby``).
    Instances are used for units and for items.
    """

    def __init__(
        self,
        aliases: Optional[Mapping[str, str]] = None,
        ignored: Iterable[str] = (),
        verified: Optional[Iterable[str]] = None,
    ) -> None:
        self._aliases: Dict[str, str] = {}
        self._canonical: Dict[str, str] = {}
        self._ignored = {match_key(x) for x in ignored if match_key(x)}
        # ``None`` means "no allow-list configured"; an empty set means
        # "allow-list configured but nothing verified yet".
        self._verified = None if verified is None else {match_key(v) for v in verified}
        for alias, target in (aliases or {}).items():
            if alias and target:
                self._aliases[match_key(alias)] = str(target)

    # -- construction ------------------------------------------------------
    @classmethod
    def from_file(cls, path: Path | str, section: Optional[str] = None) -> "NameRegistry":
        """Load a registry JSON file. Missing or invalid files yield an empty one.

        Supports both a flat ``{"aliases": ..., "ignored": ...}`` layout and a
        nested ``{"units": {...}, "items": {...}}`` layout via ``section``.
        """
        try:
            with Path(path).open("r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            return cls()
        if section and isinstance(data.get(section), Mapping):
            data = data[section]
        return cls(
            aliases=data.get("aliases", {}),
            ignored=data.get("ignored_units", data.get("ignored", [])),
            verified=data.get("verified") if "verified" in data else None,
        )

    def register_template(self, stem: str) -> None:
        """Register a template file stem as a canonical name."""
        key = match_key(stem)
        if not key or key in self._ignored:
            return
        self._canonical[key] = stem

    def add_alias(self, alias: str, target: str) -> None:
        key = match_key(alias)
        if key and target:
            self._aliases[key] = target

    # -- queries -----------------------------------------------------------
    def is_ignored(self, name: str) -> bool:
        return match_key(name) in self._ignored

    def is_verified(self, name: str) -> bool:
        """Whether ``name`` passes the optional allow-list (always true if unset)."""
        if self._verified is None:
            return True
        return self.key(name) in self._verified or match_key(name) in self._verified

    @property
    def verified(self) -> Optional[List[str]]:
        """The configured allow-list, or ``None`` when no allow-list is set."""
        return None if self._verified is None else sorted(self._verified)

    def resolve(self, name: str, cutoff: float = 0.86) -> Optional[str]:
        """Return the canonical name for ``name`` or ``None``."""
        key = match_key(name)
        if not key or key in self._ignored:
            return None
        if key in self._canonical:
            return self._canonical[key]
        if key in self._aliases:
            target = self._aliases[key]
            target_key = match_key(target)
            if target_key in self._canonical:
                return self._canonical[target_key]
            # Alias points at a name that has no template yet; keep the alias
            # target so comparisons still work.
            return target
        return self._fuzzy(key, cutoff)

    def template_stem(self, name: str) -> Optional[str]:
        """Return the template file stem (without .png) for ``name``."""
        canonical = self.resolve(name)
        if canonical is None:
            return None
        key = match_key(canonical)
        if key in self._canonical:
            return self._canonical[key]
        return canonical

    def key(self, name: str) -> str:
        """Stable comparison key for a name (canonical when possible)."""
        resolved = self.resolve(name)
        return match_key(resolved if resolved else name)

    def all_names(self) -> List[str]:
        return sorted(self._canonical.values())

    def unresolved(self, names: Iterable[str]) -> List[str]:
        """Names that are not, and cannot be, matched to a known template."""
        return [n for n in names if n and self.resolve(n) is None]

    # -- internals ---------------------------------------------------------
    def _fuzzy(self, key: str, cutoff: float) -> Optional[str]:
        if not self._canonical or len(key) < 3:
            return None
        # Refuse matches between very differently sized names: this is what
        # keeps "Topaz" from silently matching "TopazandNumby" by accident.
        candidates = [
            c for c in self._canonical
            if min(len(c), len(key)) / max(len(c), len(key)) >= 0.6
        ]
        matches = difflib.get_close_matches(key, candidates, n=1, cutoff=cutoff)
        return self._canonical[matches[0]] if matches else None


# Backwards-friendly alias used for unit registries.
UnitRegistry = NameRegistry


# ---------------------------------------------------------------------------
# Shop policy
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ShopThresholds:
    min_gold_before_roll: int = 30
    primary_roll_level: int = 8
    all_in_hp_threshold: int = 30
    interest_cap_gold: int = 30
    xp_cost: int = 4
    reroll_cost: int = 2
    pity_refreshes: int = 2

    @classmethod
    def from_strategy(cls, strategy: Mapping[str, Any], economy: Mapping[str, Any]) -> "ShopThresholds":
        thresh = strategy.get("refresh_thresholds", {}) or {}
        econ = economy or {}
        return cls(
            min_gold_before_roll=int(thresh.get("min_gold_before_roll", 30)),
            primary_roll_level=int(thresh.get("primary_roll_level", 8)),
            all_in_hp_threshold=int(thresh.get("all_in_hp_threshold", 30)),
            interest_cap_gold=int(econ.get("interest_cap_gold", 30)),
            xp_cost=int(econ.get("xp_purchase_cost", 4)),
            reroll_cost=int(econ.get("base_reroll_cost", 2)),
            pity_refreshes=int(econ.get("protagonist_bond_pity_refreshes", 2)),
        )


@dataclass(frozen=True)
class ShopState:
    gold: Optional[int] = None
    level: Optional[int] = None
    hp: Optional[int] = None
    pity: int = 0
    protagonist_in_shop: bool = False
    refreshed_this_cycle: bool = False
    buy_enabled: bool = True
    xp_enabled: bool = True


@dataclass(frozen=True)
class ShopPlan:
    level_up: bool = False
    refresh: bool = False
    reasons: Tuple[str, ...] = ()


def is_low_hp(hp: Optional[int], thresholds: ShopThresholds) -> bool:
    return hp is not None and hp <= thresholds.all_in_hp_threshold


def plan_shop(state: ShopState, thresholds: ShopThresholds) -> ShopPlan:
    """Decide whether to level up and/or reroll, with human readable reasons.

    The rules are intentionally conservative around the interest cap: leveling
    spends gold, so it only happens while we can still keep the capped interest
    bonus, unless HP is low enough that survival matters more than economy.
    """
    reasons: List[str] = []
    low_hp = is_low_hp(state.hp, thresholds)

    level_up = False
    if (
        state.xp_enabled
        and state.gold is not None
        and state.level is not None
        and state.level < thresholds.primary_roll_level
    ):
        keep_interest = state.gold >= thresholds.interest_cap_gold + thresholds.xp_cost
        if keep_interest or (low_hp and state.gold >= thresholds.xp_cost):
            level_up = True
            reasons.append(
                f"level {state.level}->{state.level + 1} "
                f"(gold {state.gold}, {'all-in' if low_hp and not keep_interest else 'interest safe'})"
            )

    refresh = False
    if state.buy_enabled and state.gold is not None:
        if state.refreshed_this_cycle:
            reasons.append("already refreshed this cycle")
        elif state.pity >= thresholds.pity_refreshes and not state.protagonist_in_shop:
            reasons.append(f"pity {state.pity}/{thresholds.pity_refreshes}: holding for protagonist")
        else:
            ready_level = state.level is not None and state.level >= thresholds.primary_roll_level
            affordable = state.gold >= thresholds.min_gold_before_roll + thresholds.reroll_cost
            all_in = low_hp and state.gold >= thresholds.reroll_cost
            if (ready_level and affordable) or all_in:
                refresh = True
                reasons.append(
                    f"reroll (gold {state.gold}, level {state.level}, "
                    f"{'all-in' if all_in and not (ready_level and affordable) else 'threshold met'})"
                )
            else:
                reasons.append("holding: below reroll threshold")

    return ShopPlan(level_up=level_up, refresh=refresh, reasons=tuple(reasons))


@dataclass(frozen=True)
class ShopUnit:
    name: str
    confidence: float
    x: int
    y: int


def select_purchases(
    shop_units: Sequence[ShopUnit],
    wanted: Iterable[str],
    protagonist: str,
    key: KeyFn = match_key,
) -> Tuple[List[ShopUnit], bool]:
    """Return the shop cards to buy and whether the protagonist was spotted."""
    wanted_keys = {key(u) for u in wanted if u}
    protagonist_key = key(protagonist) if protagonist else ""
    buys: List[ShopUnit] = []
    protagonist_seen = False
    for unit in shop_units:
        unit_key = key(unit.name)
        if protagonist_key and unit_key == protagonist_key:
            protagonist_seen = True
        if unit_key and unit_key in wanted_keys:
            buys.append(unit)
    return buys, protagonist_seen


# ---------------------------------------------------------------------------
# Crafting / equipment routing
# ---------------------------------------------------------------------------

def plan_craft(
    inventory: Sequence[Mapping[str, Any]],
    recipes: Sequence[Mapping[str, Any]],
    archetype_id: str,
    key: KeyFn = match_key,
) -> Optional[Dict[str, Any]]:
    """Return the first craftable recipe for this archetype, else ``None``.

    ``inventory`` entries need a ``slug`` (or ``name``), recipes use the
    strategy schema (``components`` list of names, ``priority_archetypes``).
    """
    available = [key(item.get("slug") or item.get("name", "")) for item in inventory]
    for recipe in recipes:
        if archetype_id not in (recipe.get("priority_archetypes") or []):
            continue
        components = [key(c) for c in recipe.get("components", [])]
        if len(components) != 2:
            continue
        # The same component twice requires two distinct inventory slots.
        idx_a = next((i for i, s in enumerate(available) if s == components[0]), None)
        idx_b = next(
            (i for i, s in enumerate(available) if s == components[1] and i != idx_a),
            None,
        )
        if idx_a is not None and idx_b is not None:
            return {
                "recipe": recipe,
                "component_a": inventory[idx_a],
                "component_b": inventory[idx_b],
            }
    return None


def choose_equip_target(
    item_name: str,
    strategy: Mapping[str, Any],
    deployed_units: Sequence[Mapping[str, Any]],
    overrides: Optional[Mapping[str, str]] = None,
    key: KeyFn = match_key,
) -> Optional[Mapping[str, Any]]:
    """Pick which deployed unit should receive ``item_name``.

    Priority: manual override > per-unit priority_items > first deployed unit.
    """
    item_key = key(item_name)
    by_key = {key(u.get("unit", "")): u for u in deployed_units}

    for override_item, preferred in (overrides or {}).items():
        if key(override_item) == item_key:
            target = by_key.get(key(preferred))
            if target is not None:
                return target

    positioning = strategy.get("unit_positioning", []) or []
    for pos in positioning:
        priority = {key(p) for p in (pos.get("priority_items") or [])}
        if item_key in priority:
            target = by_key.get(key(pos.get("unit", "")))
            if target is not None:
                return target

    for pos in positioning:
        target = by_key.get(key(pos.get("unit", "")))
        if target is not None:
            return target
    return None


# ---------------------------------------------------------------------------
# Scene / config helpers
# ---------------------------------------------------------------------------

DEFAULT_SCENES: Dict[str, str] = {
    "victory": "scenes/victory.png",
    "reward": "scenes/reward.png",
    "battle_start": "scenes/battle_start.png",
}


def resolve_scene_map(config: Mapping[str, Any]) -> Dict[str, List[str]]:
    """Normalise the ``scenes`` config section into ``{scene: [candidates]}``.

    Values may be a single relative path or a list of them. A missing or
    malformed section falls back to :data:`DEFAULT_SCENES`.
    """
    raw = config.get("scenes")
    if not isinstance(raw, Mapping) or not raw:
        return {name: [path] for name, path in DEFAULT_SCENES.items()}
    out: Dict[str, List[str]] = {}
    for name, value in raw.items():
        if isinstance(value, str):
            paths = [value]
        elif isinstance(value, (list, tuple)):
            paths = [str(v) for v in value]
        else:
            continue
        paths = [p for p in paths if p]
        if paths:
            out[str(name)] = paths
    return out or {name: [path] for name, path in DEFAULT_SCENES.items()}


def parse_roi(text: str) -> Optional[List[int]]:
    """Parse ``"x1,y1,x2,y2"`` into a list of four ints."""
    try:
        parts = [int(p.strip()) for p in str(text).split(",") if p.strip()]
    except ValueError:
        return None
    return parts if len(parts) == 4 else None


def roi_from_points(
    p1: Tuple[float, float],
    p2: Tuple[float, float],
    width: int,
    height: int,
    min_size: int = 8,
) -> Optional[List[int]]:
    """Turn two dragged corners into a clamped, ordered ``[x1, y1, x2, y2]`` box.

    Calibration UIs hand over corners in whatever order the user dragged, and
    often slightly outside the image. Returns ``None`` for boxes that would be
    too small to be a real ROI (a stray click rather than a drag).
    """
    x1, x2 = sorted((float(p1[0]), float(p2[0])))
    y1, y2 = sorted((float(p1[1]), float(p2[1])))
    x1 = max(0.0, min(float(width), x1))
    x2 = max(0.0, min(float(width), x2))
    y1 = max(0.0, min(float(height), y1))
    y2 = max(0.0, min(float(height), y2))
    if (x2 - x1) < min_size or (y2 - y1) < min_size:
        return None
    return [int(round(x1)), int(round(y1)), int(round(x2)), int(round(y2))]


def roi_center(roi: Sequence[int]) -> Tuple[int, int]:
    """Centre point of ``[x1, y1, x2, y2]`` - what a click action needs."""
    x1, y1, x2, y2 = (int(v) for v in roi)
    return (x1 + x2) // 2, (y1 + y2) // 2

