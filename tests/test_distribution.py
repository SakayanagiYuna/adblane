"""Check the installed artifact's version, license and typing contract."""
from importlib import metadata, resources
import unittest

import adblane


class DistributionTests(unittest.TestCase):
    def test_version_and_metadata(self):
        package = metadata.distribution("adblane")
        self.assertEqual(adblane.__version__, package.version)
        self.assertEqual(package.metadata["License-Expression"], "MIT")
        self.assertIn("LICENSE", package.metadata.get_all("License-File", []))
        self.assertEqual(package.requires or [], [])
        self.assertIn("Typing :: Typed", package.metadata.get_all("Classifier", []))

    def test_typed_marker_and_license_are_installed(self):
        self.assertTrue(resources.files("adblane").joinpath("py.typed").is_file())
        package = metadata.distribution("adblane")
        licenses = [entry for entry in package.files or []
                    if str(entry).endswith(".dist-info/licenses/LICENSE")]
        self.assertEqual(len(licenses), 1)
        self.assertIn("MIT License", licenses[0].read_text())


if __name__ == "__main__":
    unittest.main()
