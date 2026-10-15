"""Regression test for ImageProcessor.save_image return value (Cluster C3)."""
import os
import tempfile
import unittest

import numpy as np

from core.image_processor import ImageProcessor


class SaveImageTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.img = np.zeros((8, 8, 3), np.uint8)

    def test_returns_false_and_writes_nothing_on_encode_failure(self):
        path = os.path.join(self.dir, "bad.xyz")   # unsupported extension -> imencode fails
        ret = ImageProcessor.save_image(self.img, path)
        self.assertFalse(ret)                       # old code returned True (a lie)
        self.assertFalse(os.path.exists(path))

    def test_returns_true_and_writes_file_on_success(self):
        path = os.path.join(self.dir, "good.png")
        ret = ImageProcessor.save_image(self.img, path)
        self.assertTrue(ret)
        self.assertTrue(os.path.exists(path))


if __name__ == "__main__":
    unittest.main()
