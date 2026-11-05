"""Regression tests for failure evidence and reproducible diagnostic subsets."""
import importlib.util
import io
import tarfile
import unittest
from pathlib import Path

from worker import handler as worker

spec = importlib.util.spec_from_file_location(
    'diagnose_runpod', Path(__file__).resolve().parents[1] / 'scripts/diagnose-runpod.py'
)
diagnostic = importlib.util.module_from_spec(spec)
spec.loader.exec_module(diagnostic)


class DiagnosticsTests(unittest.TestCase):
    def test_subset_is_consecutive_deduplicated_and_identity_pinned(self):
        items = [
            {'id': str(i), 'name': f'{i:03}.jpg', 'size': 100, 'md5Checksum': str(i)}
            for i in range(205)
        ]
        items.append({**items[0], 'id': 'copy', 'name': '000-copy.jpg'})
        result = diagnostic.subset(list(reversed(items)), 200, 2)
        self.assertEqual(len(result), 200)
        self.assertEqual(result[0]['sourceId'], '2')
        self.assertEqual(result[-1]['sourceId'], '201')
        self.assertEqual(len({x['sourceId'] for x in result}), 200)
        with self.assertRaises(ValueError):
            diagnostic.subset(items, 300, 0)

    def test_failure_uploads_log_and_sparse_model_before_cleanup(self):
        uploads = {}

        class Storage:
            def put_file(self, path, source):
                uploads[path] = source.read_bytes()

            def put_bytes(self, path, content, content_type):
                uploads[path] = content

        job = {'id': 'test'}
        with self.assertRaisesRegex(RuntimeError, 'original failure'):
            with worker.diagnostic_workspace(Storage(), 'jobs/test', job) as temporary:
                root = Path(temporary)
                (root / 'survey/logs').mkdir(parents=True)
                (root / 'survey/logs/pipeline.log').write_text(
                    'Dense point cloud exited with code 245'
                )
                (root / 'survey/colmap/sparse/0').mkdir(parents=True)
                (root / 'survey/colmap/sparse/0/cameras.bin').write_bytes(b'model')
                (root / 'survey/images').mkdir()
                (root / 'survey/images/private.jpg').write_bytes(b'photo')
                raise RuntimeError('original failure')
        self.assertFalse(root.exists())
        content = uploads['jobs/test/diagnostics/failure-diagnostics.tar.gz']
        with tarfile.open(fileobj=io.BytesIO(content)) as archive:
            self.assertIn('survey/logs/pipeline.log', archive.getnames())
            self.assertIn('survey/colmap/sparse/0/cameras.bin', archive.getnames())
            self.assertNotIn('survey/images/private.jpg', archive.getnames())
        self.assertIn('diagnostics', job)

    def test_upload_failure_preserves_original_exception(self):
        class Storage:
            def put_file(self, path, source):
                raise OSError('offline')
        with self.assertRaisesRegex(RuntimeError, 'original failure'):
            with worker.diagnostic_workspace(Storage(), 'jobs/test', {'id': 'test'}):
                raise RuntimeError('original failure')

    def test_extract_native_mapper_command(self):
        self.assertEqual(diagnostic.mapper_command('$ /bin/colmap mapper --Mapper.num_threads 32'),
                         ['/bin/colmap', 'mapper', '--Mapper.num_threads', '32'])
        self.assertIsNone(
            diagnostic.mapper_command('$ python3 -m openreef.pipeline.tasks sparse-colmap')
        )


if __name__ == '__main__':
    unittest.main()
