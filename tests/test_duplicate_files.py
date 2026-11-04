"""Source identity and duplicate filename regression tests."""
import unittest
from worker.handler import select_expected_files


class DuplicateFilesTests(unittest.TestCase):
    def test_manifest_ids_preserve_distinct_photos_with_the_same_name(self):
        items = [
            {"id": "b", "name": "same.jpg", "size": 10, "md5Checksum": "bbb"},
            {"id": "a", "name": "same.jpg", "size": 10, "md5Checksum": "aaa"},
        ]
        manifest = [
            {"sourceId": "a", "sourceName": "same.jpg", "sourceMd5": "aaa", "size": 10, "name": "same.jpg"},
            {"sourceId": "b", "sourceName": "same.jpg", "sourceMd5": "bbb", "size": 10, "name": "same__2.jpg"},
        ]
        chosen = select_expected_files(items, manifest)
        self.assertEqual([file["id"] for file in chosen], ["a", "b"])
        self.assertEqual([file["name"] for file in chosen], ["same.jpg", "same__2.jpg"])
        self.assertNotEqual(chosen[0]["_source_identity"], chosen[1]["_source_identity"])
        self.assertEqual(items[0]["name"], "same.jpg")
        with self.assertRaisesRegex(RuntimeError, "missing"):
            select_expected_files(items[:1], manifest)
        for changes in ({"sourceMd5": "changed"}, {"size": 99}, {"sourceName": "renamed.jpg"}):
            with self.assertRaisesRegex(RuntimeError, "changed since submission"):
                select_expected_files(items, [{**manifest[0], **changes}])

    def test_legacy_jobs_accept_identical_copies_and_reject_ambiguous_names(self):
        items = [
            {"id": "b", "name": "same.jpg", "size": 10, "md5Checksum": "aaa"},
            {"id": "a", "name": "same.jpg", "size": 10, "md5Checksum": "aaa"},
        ]
        self.assertEqual(select_expected_files(items, ["same.jpg"])[0]["id"], "a")
        for checksum in ("bbb", None):
            with self.assertRaisesRegex(RuntimeError, "Ambiguous duplicate"):
                select_expected_files([items[0], {**items[1], "md5Checksum": checksum}], ["same.jpg"])

    def test_unverified_content_does_not_reuse_identity_cache(self):
        items = [{"id": "a", "name": "same.jpg", "size": 10}]
        manifest = [{"sourceId": "a", "sourceName": "same.jpg", "name": "same.jpg", "size": 10}]
        self.assertTrue(select_expected_files(items, manifest)[0]["_disable_source_cache"])


if __name__ == "__main__":
    unittest.main()
