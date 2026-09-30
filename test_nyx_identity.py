"""Tests for the canonical Nyx visual identity (brand/nyx_identity/) and its
consumption by carousel_handoff.py. Founder-locked 2026-09-22 (photorealistic
master reference sheets, superseding the prior anime/JRPG spec). These tests
exist to catch identity CONFIGURATION drift -- someone hand-editing the JSON
into an incomplete/inconsistent state, or a future edit reintroducing old
anime-era language into a POSITIVE identity field.
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path

import carousel_handoff as ch
import state as st

ROOT = Path(__file__).resolve().parent
SPEC_PATH = ROOT / "brand" / "nyx_identity" / "identity_spec.json"

OLD_TERMS = ("anime", "jrpg", "cel-shaded", "lavender", "silver hair", "silver-white",
             "celestial-print", "star-charm hairpin", "crescent-moon pendant")
# Physical/style traits only -- excludes old-era PROP names ("star-charm hairpin",
# "crescent-moon pendant") that legitimately keep appearing in unrelated story/prop
# continuity text (e.g. content_items[20] continues content_items[16]'s hairpin prop);
# those are narrative continuity, not part of Nyx's own superseded physical identity.
OLD_IDENTITY_TERMS = ("anime", "jrpg", "cel-shaded", "lavender", "silver hair", "silver-white",
                      "celestial-print")


class IdentitySpecFile(unittest.TestCase):
    """The spec file itself: exists, is valid, and is internally consistent."""

    @classmethod
    def setUpClass(cls):
        cls.spec = json.loads(SPEC_PATH.read_text())

    def test_master_reference_images_exist_on_disk(self):
        for ref in self.spec["reference_hierarchy"]:
            p = ROOT / ref["project_file"]
            self.assertTrue(p.is_file(), f"{ref['role']} missing at {p}")
            self.assertGreater(p.stat().st_size, 1000, f"{ref['role']} looks truncated")

    def test_reference_hierarchy_is_ranked_primary_first(self):
        refs = self.spec["reference_hierarchy"]
        self.assertEqual(len(refs), 2)
        self.assertEqual(refs[0]["role"], "primary_master")
        self.assertEqual(refs[0]["rank"], 1)
        self.assertEqual(refs[1]["role"], "secondary_supporting")
        self.assertEqual(refs[1]["rank"], 2)
        self.assertIn("HIGHEST authority", refs[0]["authority"])
        self.assertIn("never", refs[1]["authority"].lower())  # never override the primary

    def test_every_locked_trait_and_hosted_url_present(self):
        for key in ("facial_identity", "eyes", "hair", "fox_ears", "tail", "skin",
                    "beauty_mark", "body", "age", "ethnicity_phenotype", "realism_level"):
            self.assertTrue(str(self.spec["locked_traits"].get(key, "")).strip(), key)
        for ref in self.spec["reference_hierarchy"]:
            self.assertTrue(ref["hosted_url"].startswith("https://"), ref["role"])

    def test_beauty_mark_is_locked_anatomically_and_by_viewer_side(self):
        # reference_primary shows the mark on Nyx's anatomical LEFT cheek, i.e.
        # viewer-right (founder correction 2026-09-24; v2.0.0 wrongly said "RIGHT
        # cheek"). Both frames are stated so no model can mirror it silently.
        # The superseded anime-era "star freckle under the LEFT eye" must not return.
        text = self.spec["locked_traits"]["beauty_mark"].lower()
        self.assertIn("anatomical left cheek", text)
        self.assertIn("viewer-right", text)
        self.assertNotIn("right cheek", text)
        self.assertNotIn("under the left eye", text)
        self.assertNotIn("star", text)

    def test_exactly_two_fox_ears_and_no_human_ears(self):
        ears = self.spec["locked_traits"]["fox_ears"].lower()
        self.assertIn("exactly two visible ears", ears)
        for banned in ("human ears", "duplicate ears", "costume/headband ears", "four-ear"):
            self.assertIn(banned, ears)
        self.assertIn("master reference", self.spec["locked_traits"]["reference_precedence"].lower())

    def test_realism_is_required_not_prohibited(self):
        self.assertIn("photorealistic", self.spec["locked_traits"]["realism_level"].lower())
        self.assertTrue(any("photorealistic" in v.lower() or "realistic" in v.lower()
                            for v in self.spec["reference_prose"].values()))

    def test_prohibited_drift_blocks_the_old_style_and_allowed_variation_permits_wardrobe(self):
        prohibited = " ".join(self.spec["prohibited_drift"]).lower()
        self.assertIn("anime", prohibited)
        self.assertIn("cartoon", prohibited)
        allowed = [a.lower() for a in self.spec["allowed_variation"]]
        self.assertIn("clothing / wardrobe", allowed)
        self.assertIn("environment / background", allowed)

    def test_base_prompt_has_the_requested_modular_template(self):
        tpl = self.spec["base_generation_prompt"]["template"]
        for section in ("SCENE:", "WARDROBE:", "POSE:", "CAMERA:", "LIGHTING:", "MOOD:"):
            self.assertIn(section, tpl)
        self.assertTrue(self.spec["base_generation_prompt"]["identity_block"].strip())

    def test_supersession_is_recorded_with_provenance_not_deleted(self):
        sup = self.spec["superseded_visual_identity"]
        self.assertEqual(sup["prior_spec_version"], "1.0.0")
        self.assertIn("anime", sup["prior_summary"].lower())
        self.assertTrue(sup["why_superseded"].strip())
        self.assertIn("VISUAL identity only", sup["conflict_scope"])

    def test_no_positive_field_still_carries_old_identity_language(self):
        """identity_lock/style_lock/base-prompt fields must never regress to the old
        vocabulary -- prohibited_drift/superseded_visual_identity are exempt, since
        they correctly NAME the old style in order to forbid or archive it."""
        positive = json.dumps({
            "locked_traits": self.spec["locked_traits"],
            "reference_prose": self.spec["reference_prose"],
            "base_generation_prompt": self.spec["base_generation_prompt"],
            "character": self.spec["character"],
        }).lower()
        for term in OLD_IDENTITY_TERMS:
            self.assertNotIn(term, positive, f"old-identity term {term!r} leaked into a positive field")


class CarouselHandoffLoadsTheCanonicalSpec(unittest.TestCase):
    """carousel_handoff.py must not hardcode identity -- it loads this file."""

    def test_module_constants_are_derived_from_the_spec_file(self):
        spec = json.loads(SPEC_PATH.read_text())
        self.assertEqual(ch.MIN_CHARACTER_AGE, spec["character"]["minimum_age"])
        self.assertEqual(ch.MASTER_REFERENCE_URL, spec["reference_hierarchy"][0]["hosted_url"])
        self.assertEqual(ch.CANONICAL_PORTRAIT, ROOT / spec["reference_hierarchy"][0]["project_file"])
        self.assertEqual(set(ch.IDENTITY_ANCHORS), {"face", "eyes", "hair", "ears", "tail",
                                                     "skin_and_marks", "body", "realism"})

    def test_a_missing_spec_file_fails_closed_not_silently(self):
        with self.assertRaises(ch.HandoffError):
            ch.load_identity_spec(ROOT / "brand" / "nyx_identity" / "does_not_exist.json")

    def test_identity_anchors_never_regress_to_old_vocabulary(self):
        blob = json.dumps(ch.IDENTITY_ANCHORS).lower() + ch.ADULT_AGE_LOCK.lower()
        for term in OLD_IDENTITY_TERMS:
            self.assertNotIn(term, blob, term)

    def test_negative_identity_still_forbids_the_old_style_and_keeps_the_sfw_guardrail(self):
        blob = " ".join(ch.NEGATIVE_IDENTITY).lower()
        self.assertIn("anime", blob)
        self.assertIn("cartoon", blob)
        self.assertIn("no nudity", blob)          # compliance guardrail, unrelated to the restyle
        self.assertIn("real-person likeness", blob)

    def test_negative_constraints_require_photorealism_not_forbid_it(self):
        blob = " ".join(ch.NEGATIVE_CONSTRAINTS).lower()
        self.assertNotIn("not photorealistic", blob)   # the OLD (now-backwards) rule
        self.assertIn("photorealistic", blob)

    def test_canonical_references_lead_with_the_primary_master_and_include_the_secondary(self):
        refs = ch.canonical_references({"reference_image_url": ""}, index=None)
        notes = " ".join(r["note"] for r in refs)
        self.assertIn("primary master reference", refs[0]["note"])
        self.assertIn("secondary supporting reference", notes)
        self.assertIn("never overrides the primary", notes)

    def test_canonical_references_prefers_personas_own_url_but_falls_back_to_the_master(self):
        custom = ch.canonical_references({"reference_image_url": "https://custom/x.png"})
        self.assertEqual(custom[0]["value"], "https://custom/x.png")
        fallback = ch.canonical_references({})
        self.assertEqual(fallback[0]["value"], ch.MASTER_REFERENCE_URL)

    def test_image_prompt_never_says_illustration_and_always_states_photorealism(self):
        item = {"aspect_ratio": "4:5"}
        slide = {"beat": "b", "composition": "c", "continuity": ""}
        prompt = ch._image_prompt(item, {"visual_style": "", "visual_description": ""}, slide).lower()
        self.assertNotIn("illustration", prompt)
        self.assertIn("photorealistic", prompt)


class VenturePersonaIsSyncedToTheSpec(unittest.TestCase):
    """state/venture.json persona must mirror the canonical spec, with the old
    values preserved (not deleted) for provenance."""

    @classmethod
    def setUpClass(cls):
        cls.v = st.load()
        cls.persona = cls.v["persona"]
        cls.spec = json.loads(SPEC_PATH.read_text())

    def test_persona_visual_fields_match_the_canonical_spec_exactly(self):
        self.assertEqual(self.persona["visual_description"], self.spec["reference_prose"]["visual_description"])
        self.assertEqual(self.persona["visual_style"], self.spec["reference_prose"]["visual_style"])
        self.assertEqual(self.persona["reference_image_url"], self.spec["reference_hierarchy"][0]["hosted_url"])

    def test_persona_points_at_the_spec_file_and_records_its_version(self):
        self.assertEqual(self.persona["identity_spec_path"], "brand/nyx_identity/identity_spec.json")
        self.assertEqual(self.persona["identity_spec_version"], self.spec["spec_version"])

    def test_old_persona_values_are_preserved_not_deleted(self):
        sup = self.persona["superseded_visual_identity"]
        self.assertIn("anime", sup["visual_style"].lower())
        self.assertIn("silver hair", sup["visual_description"].lower())
        self.assertTrue(sup["superseded_at"])

    def test_persona_no_longer_positively_asserts_anime_styling(self):
        for field in ("visual_style", "visual_description", "niche"):
            for term in OLD_TERMS:
                self.assertNotIn(term, self.persona[field].lower(), f"{field} still says {term!r}")

    def test_identity_pack_built_from_the_real_persona_has_no_problems(self):
        pack = ch.identity_pack(self.persona)
        self.assertEqual(ch.identity_pack_problems(pack), [])


class Item20UsesTheLockedIdentity(unittest.TestCase):
    """content_items[20] (story carousel) was rebuilt against the new identity;
    its stale anime-era local files were archived, not deleted."""

    @classmethod
    def setUpClass(cls):
        cls.package_path = ROOT / "generated_assets" / "carousel_item20" / "generation_package.json"
        cls.pkg = json.loads(cls.package_path.read_text())

    def test_package_is_the_current_relocatable_version(self):
        self.assertEqual(self.pkg["package_version"], 4)

    def test_every_slide_prompt_is_clean_of_old_identity_language(self):
        for slide in self.pkg["slides"]:
            prompt = slide["image_prompt"].lower()
            for term in OLD_IDENTITY_TERMS:
                self.assertNotIn(term, prompt, f"slide {slide['slot']} still says {term!r}")
            self.assertIn("photorealistic", prompt)

    def test_continuity_sheet_identity_pack_is_complete(self):
        identity = self.pkg["continuity_sheet"]["identity"]
        self.assertEqual(ch.identity_pack_problems(identity), [])

    def test_stale_anime_era_local_files_were_archived_not_deleted(self):
        archive = ROOT / "generated_assets" / "carousel_item20" / "superseded_v1_anime"
        self.assertTrue(archive.is_dir())
        archived_names = {p.name for p in archive.iterdir()}
        self.assertTrue({"ref.png", "ref_small.png"}.issubset(archived_names))

    def test_item20_lifecycle_untouched_by_the_rebuild(self):
        v = st.load()
        # write_package() is a local metadata write only -- it must never itself
        # advance lifecycle_status (that stays gated on real slide delivery + QA).
        self.assertEqual(v["content_plan"]["content_items"][20]["lifecycle_status"], "BRIEF_READY")


if __name__ == "__main__":
    unittest.main()
