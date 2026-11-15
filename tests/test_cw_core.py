"""Unit tests for cw_core - the dependency-free decision logic.

Run from the project root:

    python -m unittest discover -s tests -v
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cw_core import (  # noqa: E402
    DEFAULT_SCENES,
    NameRegistry,
    ShopState,
    ShopThresholds,
    ShopUnit,
    choose_equip_target,
    coerce_number,
    match_key,
    parse_roi,
    plan_craft,
    plan_shop,
    resolve_scene_map,
    roi_center,
    roi_from_points,
    select_purchases,
    slugify,
)


class TestNaming(unittest.TestCase):
    def test_slugify(self):
        self.assertEqual(slugify("Topaz and Numby"), "topaz_and_numby")
        self.assertEqual(slugify("  Anti-Grav Boots  "), "anti_grav_boots")
        self.assertEqual(slugify("March 7th"), "march_7th")

    def test_match_key_ignores_punctuation_and_case(self):
        self.assertEqual(match_key("Topaz and Numby"), "topazandnumby")
        self.assertEqual(match_key("T0PAZ AND NUMBY"), "t0pazandnumby")
        self.assertEqual(match_key("TopazandNumby"), "topazandnumby")
        self.assertEqual(match_key("High-frequency Chainsaw"), "highfrequencychainsaw")


class TestNameRegistry(unittest.TestCase):
    def setUp(self):
        self.registry = NameRegistry(aliases={"Topaz": "TopazandNumby", "March 7th": "March7th"})
        self.registry.register_template("TopazandNumby")
        self.registry.register_template("March7th")
        self.registry.register_template("Aglaea")

    def test_alias_to_template(self):
        self.assertEqual(self.registry.resolve("Topaz"), "TopazandNumby")
        self.assertEqual(self.registry.resolve("Topaz and Numby"), "TopazandNumby")
        self.assertEqual(self.registry.template_stem("Topaz"), "TopazandNumby")

    def test_key_makes_names_comparable(self):
        self.assertEqual(self.registry.key("Topaz"), self.registry.key("TopazandNumby"))
        self.assertEqual(self.registry.key("March 7th"), self.registry.key("March7th"))
        self.assertNotEqual(self.registry.key("Aglaea"), self.registry.key("March7th"))

    def test_exact_template_and_fuzzy(self):
        self.assertEqual(self.registry.resolve("Aglaea"), "Aglaea")
        self.assertEqual(self.registry.resolve("aglaea"), "Aglaea")
        # OCR-style typo, still same length ballpark
        self.assertEqual(self.registry.resolve("Aglaea"), "Aglaea")

    def test_fuzzy_rejects_different_lengths(self):
        self.assertIsNone(self.registry.resolve("Ag"))

    def test_unresolved(self):
        self.assertEqual(self.registry.unresolved(["Topaz", "Aglaea"]), [])
        self.assertEqual(self.registry.unresolved(["Nobodyatall"]), ["Nobodyatall"])

    def test_ignored_names_never_resolve(self):
        registry = NameRegistry(ignored=["slot0_capture", "e1"])
        registry.register_template("e1")
        self.assertIsNone(registry.resolve("e1"))
        self.assertIsNone(registry.template_stem("slot0_capture"))

    def test_verified_allow_list(self):
        self.assertTrue(NameRegistry().is_verified("anything"))
        locked = NameRegistry(verified=[])
        self.assertFalse(locked.is_verified("anything"))
        allowed = NameRegistry(verified=["Energy Sail"])
        self.assertTrue(allowed.is_verified("energy_sail"))

    def test_missing_file_yields_empty_registry(self):
        registry = NameRegistry.from_file(Path("does-not-exist.json"), section="units")
        self.assertEqual(registry.all_names(), [])


class TestCoerceNumber(unittest.TestCase):
    def test_values(self):
        self.assertEqual(coerce_number("12"), 12)
        self.assertEqual(coerce_number(" 3.5 "), 3.5)
        self.assertEqual(coerce_number("", fallback=7), 7)
        self.assertEqual(coerce_number("abc", fallback=7), 7)
        self.assertIsNone(coerce_number(None, fallback=None))
        self.assertEqual(coerce_number(None, fallback=0), 0)
        self.assertTrue(coerce_number(True))

    def test_bad_input_keeps_original_without_fallback(self):
        self.assertEqual(coerce_number("abc"), "abc")


class TestParseRoi(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(parse_roi("10,20,30,40"), [10, 20, 30, 40])
        self.assertEqual(parse_roi(" 10 , 20 , 30 , 40 "), [10, 20, 30, 40])

    def test_invalid(self):
        self.assertIsNone(parse_roi("1,2,3"))
        self.assertIsNone(parse_roi("a,b,c,d"))
        self.assertIsNone(parse_roi(""))


class TestSceneMap(unittest.TestCase):
    def test_defaults_when_missing(self):
        result = resolve_scene_map({})
        self.assertEqual(set(result), set(DEFAULT_SCENES))
        self.assertEqual(result["victory"], [DEFAULT_SCENES["victory"]])

    def test_accepts_lists_and_drops_empty(self):
        result = resolve_scene_map({"scenes": {"victory": ["a.png", ""], "reward": "b.png", "bad": 5}})
        self.assertEqual(result, {"victory": ["a.png"], "reward": ["b.png"]})


class TestPlanShop(unittest.TestCase):
    thresholds = ShopThresholds()

    def plan(self, **kwargs):
        return plan_shop(ShopState(**kwargs), self.thresholds)

    def test_levels_up_while_protecting_interest(self):
        plan = self.plan(gold=40, level=5, hp=90)
        self.assertTrue(plan.level_up)
        self.assertFalse(plan.refresh)  # below reroll level
        self.assertTrue(any("interest safe" in r for r in plan.reasons))

    def test_does_not_level_up_when_it_would_break_interest(self):
        plan = self.plan(gold=31, level=5, hp=90)
        self.assertFalse(plan.level_up)

    def test_refreshes_at_threshold(self):
        plan = self.plan(gold=32, level=8, hp=90)
        self.assertTrue(plan.refresh)
        self.assertFalse(plan.level_up)

    def test_does_not_refresh_below_threshold(self):
        plan = self.plan(gold=31, level=8, hp=90)
        self.assertFalse(plan.refresh)
        self.assertTrue(any("holding" in r for r in plan.reasons))

    def test_only_one_refresh_per_cycle(self):
        plan = self.plan(gold=100, level=8, hp=90, refreshed_this_cycle=True)
        self.assertFalse(plan.refresh)
        self.assertTrue(any("already refreshed" in r for r in plan.reasons))

    def test_pity_holds_for_protagonist(self):
        plan = self.plan(gold=100, level=8, hp=90, pity=2)
        self.assertFalse(plan.refresh)
        self.assertTrue(any("pity 2/2" in r for r in plan.reasons))

    def test_pity_does_not_block_when_protagonist_visible(self):
        plan = self.plan(gold=100, level=8, hp=90, pity=2, protagonist_in_shop=True)
        self.assertTrue(plan.refresh)

    def test_low_hp_all_in(self):
        plan = self.plan(gold=2, level=3, hp=20)
        self.assertTrue(plan.refresh)
        self.assertFalse(plan.level_up)  # cannot afford xp
        self.assertTrue(any("all-in" in r for r in plan.reasons))

    def test_low_hp_can_level_when_gold_is_short(self):
        plan = self.plan(gold=5, level=3, hp=20)
        self.assertTrue(plan.level_up)

    def test_disabled_toggles_do_nothing(self):
        plan = self.plan(gold=100, level=8, hp=90, buy_enabled=False, xp_enabled=False)
        self.assertFalse(plan.refresh)
        self.assertFalse(plan.level_up)

    def test_unknown_hud_values_do_not_trigger_actions(self):
        plan = self.plan(gold=None, level=None, hp=None)
        self.assertFalse(plan.refresh)
        self.assertFalse(plan.level_up)

    def test_thresholds_come_from_strategy_json(self):
        thresholds = ShopThresholds.from_strategy(
            {"refresh_thresholds": {"min_gold_before_roll": 20, "primary_roll_level": 6}},
            {"interest_cap_gold": 25, "xp_purchase_cost": 5, "base_reroll_cost": 3},
        )
        self.assertEqual(thresholds.min_gold_before_roll, 20)
        self.assertEqual(thresholds.primary_roll_level, 6)
        self.assertEqual(thresholds.interest_cap_gold, 25)
        self.assertEqual(thresholds.xp_cost, 5)
        self.assertEqual(thresholds.reroll_cost, 3)


class TestSelectPurchases(unittest.TestCase):
    def test_buys_wanted_and_spots_protagonist(self):
        shop = [
            ShopUnit("Aglaea", 0.9, 1, 2),
            ShopUnit("Topaz and Numby", 0.8, 3, 4),
            ShopUnit("Herta", 0.7, 5, 6),
        ]
        registry = NameRegistry(aliases={"Topaz": "TopazandNumby"})
        registry.register_template("TopazandNumby")
        registry.register_template("Aglaea")

        buys, seen = select_purchases(shop, ["Topaz", "Aglaea"], "Topaz", key=registry.key)
        self.assertTrue(seen)
        self.assertEqual([u.name for u in buys], ["Aglaea", "Topaz and Numby"])

    def test_no_match(self):
        buys, seen = select_purchases([ShopUnit("Herta", 0.9, 1, 1)], ["Aglaea"], "Topaz")
        self.assertEqual(buys, [])
        self.assertFalse(seen)


RECIPES = [
    {
        "item_id": "roller_skates",
        "name": "Roller Skates",
        "components": ["Weapon Master", "Weapon Master"],
        "priority_archetypes": ["aglaea"],
    },
    {
        "item_id": "photon_cell",
        "name": "Photon Cell",
        "components": ["Anti-Grav Boots", "Weapon Master"],
        "priority_archetypes": ["aglaea"],
    },
]


class TestPlanCraft(unittest.TestCase):
    def test_requires_two_distinct_slots_for_duplicate_components(self):
        inventory = [{"slug": "weapon_master", "name": "Weapon Master"}]
        self.assertIsNone(plan_craft(inventory, RECIPES, "aglaea"))

    def test_crafts_when_both_slots_exist(self):
        inventory = [
            {"slug": "weapon_master", "name": "Weapon Master"},
            {"slug": "weapon_master", "name": "Weapon Master"},
        ]
        result = plan_craft(inventory, RECIPES, "aglaea")
        self.assertIsNotNone(result)
        self.assertEqual(result["recipe"]["item_id"], "roller_skates")

    def test_skips_recipes_for_other_archetypes(self):
        inventory = [
            {"slug": "anti_grav_boots", "name": "Anti-Grav Boots"},
            {"slug": "weapon_master", "name": "Weapon Master"},
        ]
        self.assertIsNone(plan_craft(inventory, RECIPES, "hysilens"))

    def test_component_names_are_matched_loosely(self):
        inventory = [
            {"slug": "Anti Grav Boots", "name": "Anti Grav Boots"},
            {"slug": "Weapon-Master", "name": "Weapon-Master"},
        ]
        result = plan_craft(inventory, RECIPES[1:], "aglaea")
        self.assertIsNotNone(result)


class TestChooseEquipTarget(unittest.TestCase):
    strategy = {
        "id": "aglaea",
        "unit_positioning": [
            {"unit": "Aglaea", "slot_index": 0, "priority_items": ["Weapon Master"]},
            {"unit": "Sunday", "slot_index": 1, "priority_items": []},
            {"unit": "Herta", "slot_index": 2, "priority_items": []},
        ],
    }
    deployed = [
        {"unit": "Herta", "slot_index": 2},
        {"unit": "Sunday", "slot_index": 1},
    ]

    def test_priority_items_win(self):
        deployed = self.deployed + [{"unit": "Aglaea", "slot_index": 0}]
        target = choose_equip_target("Weapon Master", self.strategy, deployed)
        self.assertEqual(target["unit"], "Aglaea")

    def test_override_beats_priority_items(self):
        deployed = self.deployed + [{"unit": "Aglaea", "slot_index": 0}]
        target = choose_equip_target(
            "Weapon Master", self.strategy, deployed, overrides={"Weapon Master": "Herta"}
        )
        self.assertEqual(target["unit"], "Herta")

    def test_falls_back_to_first_deployed_unit(self):
        target = choose_equip_target("Photon Cell", self.strategy, self.deployed)
        self.assertEqual(target["unit"], "Sunday")

    def test_none_when_nothing_deployed(self):
        self.assertIsNone(choose_equip_target("Photon Cell", self.strategy, []))


class TestRoiGeometry(unittest.TestCase):
    def test_points_in_any_order(self):
        self.assertEqual(roi_from_points((300, 200), (100, 50), 1920, 1080), [100, 50, 300, 200])

    def test_clamped_to_frame(self):
        self.assertEqual(
            roi_from_points((-40, -10), (40, 30), 1920, 1080), [0, 0, 40, 30]
        )
        self.assertEqual(
            roi_from_points((1900, 1070), (2000, 1200), 1920, 1080), [1900, 1070, 1920, 1080]
        )

    def test_rounds_floats(self):
        # round() is banker's rounding in Python: 30.5 -> 30, 20.6 -> 21.
        self.assertEqual(roi_from_points((10.4, 20.6), (30.5, 40.2), 1920, 1080), [10, 21, 30, 40])

    def test_tiny_drag_is_rejected(self):
        self.assertIsNone(roi_from_points((100, 100), (102, 103), 1920, 1080))
        self.assertIsNone(roi_from_points((100, 100), (100, 100), 1920, 1080))

    def test_centre(self):
        self.assertEqual(roi_center([100, 200, 200, 300]), (150, 250))
        self.assertEqual(roi_center([0, 0, 41, 41]), (20, 20))


if __name__ == "__main__":
    unittest.main()
