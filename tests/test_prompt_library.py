import unittest
from pathlib import Path

from skills.library import PLACEHOLDER_RE, filter_library, load_library, render_prompt

LIBRARY_ROOT = Path(__file__).resolve().parents[1] / "skills"
REQUIRED_FIELDS = {"id", "typ", "kategorie", "tags", "titel", "beschreibung", "version"}
CATEGORIES = {"schreiben", "code", "analyse", "kreativ", "business", "lernen"}


class PromptLibraryTest(unittest.TestCase):
    def setUp(self):
        self.entries = load_library(LIBRARY_ROOT)

    def test_all_required_fields_present(self):
        self.assertGreaterEqual(len(self.entries), 18)
        for entry in self.entries:
            self.assertTrue(REQUIRED_FIELDS.issubset(entry), entry.get("id"))
            if entry["typ"] == "prompt":
                self.assertIn("prompt", entry, entry.get("id"))
                self.assertNotIn("pipeline", entry, entry.get("id"))
            else:
                self.assertEqual("skill", entry["typ"])
                self.assertIn("pipeline", entry, entry.get("id"))
                self.assertNotIn("prompt", entry, entry.get("id"))

    def test_every_category_has_minimum_entries(self):
        self.assertEqual(CATEGORIES, {entry["kategorie"] for entry in self.entries})
        for category in CATEGORIES:
            matches = filter_library(category=category, entries=self.entries)
            self.assertGreaterEqual(len(matches), 3, category)

    def test_prompts_use_placeholder_syntax(self):
        for entry in self.entries:
            if entry["typ"] == "prompt":
                self.assertTrue(PLACEHOLDER_RE.search(entry["prompt"]), entry["id"])
            else:
                for step in entry["pipeline"]:
                    self.assertTrue(PLACEHOLDER_RE.search(step["prompt"]), entry["id"])

    def test_filter_by_category_and_tag(self):
        category_matches = filter_library(category="code", entries=self.entries)
        self.assertEqual({"bug-fix-workflow", "code-review", "sql-generator"}, {item["id"] for item in category_matches})
        tag_matches = filter_library(tag="#business", entries=self.entries)
        self.assertIn("email-antwort", {item["id"] for item in tag_matches})
        self.assertTrue(all("business" in item["tags"] for item in tag_matches))
        combined = filter_library(category="code", tag="code", entries=self.entries)
        self.assertIn("code-review", {item["id"] for item in combined})

    def test_render_prompt_replaces_variables(self):
        entry = next(item for item in self.entries if item["id"] == "email-antwort")
        rendered = render_prompt(entry, input="Bitte bestätigen Sie den Termin.")
        self.assertIn("Bitte bestätigen Sie den Termin.", rendered)
        self.assertNotIn("{{input}}", rendered)

    def test_render_prompt_reports_missing_variables(self):
        entry = next(item for item in self.entries if item["id"] == "erklaer-einfach")
        with self.assertRaises(ValueError):
            render_prompt(entry)


if __name__ == "__main__":
    unittest.main()
