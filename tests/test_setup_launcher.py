"""Portable checks for the Mac setup launcher's credential and SSH handling."""
import importlib.util
import io
from pathlib import Path
import tarfile
import tempfile
import unittest

spec = importlib.util.spec_from_file_location(
    'openreef_setup', Path(__file__).resolve().parents[1] / 'setup/setup.py'
)
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)


class SetupTests(unittest.TestCase):
    def test_ssh_options_are_normalized_without_shell_evaluation(self):
        result = setup.ssh_connection('ssh root@host.example -p 1234 -i ~/.ssh/runpod_key')
        self.assertEqual(result[:3], ['ssh', '-p', '1234'])
        self.assertEqual(result[-1], 'root@host.example')
        self.assertTrue(result[-2].endswith('/.ssh/runpod_key'))
        for command in ('ssh host ls', 'ssh host;touch /tmp/unsafe', 'ssh -p', ''):
            with self.assertRaises(RuntimeError):
                setup.ssh_connection(command)

    def test_env_roundtrip_retains_quotes_and_does_not_execute_values(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'drive.env'
            values = {'GOOGLE_REFRESH_TOKEN': 'quotes " and $(touch /tmp/unsafe)',
                      'GOOGLE_CLIENT_ID': 'example'}
            setup.write_env(path, values)
            self.assertEqual(setup.read_env(path), values)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_unpack_rejects_path_traversal(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / 'source.tar.gz'
            with tarfile.open(path, 'w:gz') as archive:
                content = b'bad'
                item = tarfile.TarInfo('../outside')
                item.size = len(content)
                archive.addfile(item, io.BytesIO(content))
            with self.assertRaises(RuntimeError):
                setup.unpack(path, root / 'install')
            self.assertFalse((root / 'outside').exists())


if __name__ == '__main__':
    unittest.main()
